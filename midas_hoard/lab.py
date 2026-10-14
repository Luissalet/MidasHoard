"""The strategy lab: saved strategies, backtest runs, the experiments log and validation.

Every run — good, bad, rejected or crashed — is one row of ``experiments``; variants are never deleted, because the
count of variants tried is what makes a metric honest. A dev/holdout split seals the holdout: the dev run is cut at the
split date in code, and looking at the holdout is an explicit, counted act.
"""

from __future__ import annotations

import csv
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from . import duplicates as dup
from . import health as health_mod
from . import labreport
from . import reports as reports_mod
from . import validation as val
from .backtest import BacktestOutput, prepare_universe, run_backtest
from .db import Database
from .errors import MidasError
from .hoard_link.atomic import write_json_atomic, write_text_atomic
from .hoard_link.artifacts import ArtifactRef
from .patching import merge_patch
from .snapshots import Snapshot, SnapshotStore, provenance
from .strategy import spec_hash, validate_spec

METHODS = ("walk_forward", "permutation", "bootstrap", "multiple_testing")


def _plain(doc: Any) -> Any:
    """The document with anything JSON cannot hold (numpy numbers, timestamps) written as text, as before."""
    return json.loads(json.dumps(doc, default=str))


class Lab:
    def __init__(self, db: Database, store: SnapshotStore, runs_dir: Path, *, emit: Callable[[str, dict[str, Any]], None] = lambda t, d: None,
                 clock: Callable[[], float] = time.time, embedder: Optional[Callable[[list[str]], list[list[float]]]] = None):
        self.db, self.store, self.runs_dir, self.emit, self.clock = db, store, Path(runs_dir), emit, clock
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Snapshot] = {}
        self.embedder = embedder  # texts -> vectors (Hoard Link / Borges); None -> lexical TF-IDF for the description similarity
        self._symbols: dict[str, str] = {}
        self._features: dict[str, dict[str, Any]] = {}
        self._related: dict[tuple, dict[str, Any]] = {}

    # ---------------------------------------------------------------- data
    def load(self, snapshot_id: str) -> Snapshot:
        """Snapshot loader with a per-process cache (snapshots are immutable, their hash is verified on first load)."""
        snap = self._cache.get(snapshot_id)
        if snap is None:
            snap = self.store.load(snapshot_id)
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[snapshot_id] = snap
        return snap

    def _exists(self, snapshot_id: str) -> bool:
        return self.db.one("SELECT 1 FROM snapshots WHERE id = ?", (snapshot_id,)) is not None

    def validate(self, spec: Any) -> dict[str, Any]:
        res = validate_spec(spec, snapshot_exists=self._exists)
        if res["ok"]:
            spec = res["normalized"]
            metas = [self.store.meta(s) for s in spec["universe"]]
            currencies = {m["currency"] for m in metas if m["currency"]}
            issues = []
            if len(currencies) > 1 and not spec.get("fx"):
                issues.append({"path": "fx", "message": f"The universe mixes currencies ({', '.join(sorted(currencies))}).",
                               "hint": 'Add "currency": "EUR" and "fx": {"<asset snapshot id>": "<exchange-rate snapshot id>"} so the conversion is explicit and recorded.'})
            if spec["execution"] == "next_open" and any("open" not in m["columns"] for m in metas):
                issues.append({"path": "execution", "message": "next_open needs an open column, which some universe snapshots lack.",
                               "hint": "Use execution 'close' for series without OHLC (FRED, ECB, CoinGecko)."})
            if len({m["frequency"] for m in metas}) > 1:
                issues.append({"path": "universe", "message": "The universe mixes frequencies.", "hint": "Resample is not applied implicitly: fetch each asset at the same interval."})
            if issues:
                return {"ok": False, "issues": issues}
            res["universe"] = [{"snapshot": m["id"], "symbol": m["symbol"], "provider": m["provider"], "currency": m["currency"],
                                "frequency": m["frequency"], "range": [m["actual_start"], m["actual_end"]]} for m in metas]
        return res

    # ---------------------------------------------------------- strategies
    def save_strategy(self, spec: Any, note: str = "") -> dict[str, Any]:
        check = self.validate(spec)
        if not check["ok"]:
            raise MidasError("invalid_spec", "The strategy spec is not valid: " + check["issues"][0]["message"], check["issues"][0]["hint"], issues=check["issues"])
        n = check["normalized"]
        existing = self.db.one("SELECT id FROM strategies WHERE name = ?", (n["name"],))
        now = self.clock()
        if existing:
            sid = existing["id"]
            self.db.execute("UPDATE strategies SET spec=?, spec_hash=?, note=?, updated_ts=? WHERE id=?", (json.dumps(n), check["spec_hash"], note or n["note"], now, sid))
        else:
            sid = "st_" + uuid.uuid4().hex[:8]
            self.db.execute("INSERT INTO strategies(id, name, spec, spec_hash, note, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
                            (sid, n["name"], json.dumps(n), check["spec_hash"], note or n["note"], now, now))
        return {"id": sid, "name": n["name"], "spec_hash": check["spec_hash"], "updated": bool(existing), "summary": check["summary"]}

    def patch_strategy(self, ident: str, spec_patch: dict[str, Any], note: Optional[str] = None) -> dict[str, Any]:
        """Atomic partial update of a saved strategy: merge ``spec_patch`` (objects merge, arrays replace, null removes)
        into its stored spec and save it again under the same name."""
        with self.db.transaction():
            current = self.strategy(ident)
            merged = merge_patch(current["spec"], spec_patch)
            if merged.get("name") != current["name"]:
                raise MidasError("invalid_request", "spec_patch cannot rename a strategy.", "Save the full spec under the new name instead.")
            return self.save_strategy(merged, current["note"] if note is None else note)

    def strategy(self, ident: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM strategies WHERE id = ? OR name = ?", (ident, ident))
        if row is None:
            raise MidasError("not_found", f"Unknown strategy '{ident}'.", "List them with strategies_list.")
        return {"id": row["id"], "name": row["name"], "spec": json.loads(row["spec"]), "spec_hash": row["spec_hash"], "note": row["note"],
                "created_at": row["created_ts"], "updated_at": row["updated_ts"]}

    def strategies(self) -> list[dict[str, Any]]:
        out = []
        for r in self.db.query("SELECT * FROM strategies ORDER BY updated_ts DESC, rowid DESC"):
            spec = json.loads(r["spec"])
            fam = self.family_stats(spec.get("family") or spec["name"])
            out.append({"id": r["id"], "name": r["name"], "spec_hash": r["spec_hash"], "note": r["note"], "updated_at": r["updated_ts"],
                        "universe": spec["universe"], "variants_tried": fam["variants"], "effective_variants": fam["effective_variants"], "spec": spec})
        return out

    def delete_strategy(self, ident: str) -> dict[str, Any]:
        s = self.strategy(ident)
        self.db.execute("DELETE FROM strategies WHERE id = ?", (s["id"],))
        return {"deleted": s["id"], "name": s["name"], "note": "Experiments run with it stay in the log."}

    # ----------------------------------------------------- mechanism duplicates
    def _symbol_of(self, snapshot_id: str) -> str:
        sym = self._symbols.get(snapshot_id)
        if sym is None:
            row = self.db.one("SELECT symbol FROM snapshots WHERE id = ?", (snapshot_id,))
            sym = row["symbol"] if row else snapshot_id
            self._symbols[snapshot_id] = sym
        return sym

    def _feat(self, spec: dict[str, Any], key: str = "") -> dict[str, Any]:
        """Name-free features of a spec (cached by spec hash plus the labels that feed the text similarity)."""
        ck = f"{key}|{spec.get('name', '')}|{spec.get('family', '')}|{spec.get('note', '')}" if key else ""
        if ck and ck in self._features:
            return self._features[ck]
        f = dup.spec_features(spec, self._symbol_of)
        if ck:
            if len(self._features) > 2000:
                self._features.clear()
            self._features[ck] = f
        return f

    def _exp_signature(self) -> tuple:
        r = self.db.one("SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS m FROM experiments")
        return (r["n"], r["m"])

    def _variant_rows(self) -> list[dict[str, Any]]:
        """One row per (family, spec_hash) of the successful backtests and walk-forward candidates, the oldest first."""
        rows = self.db.query("SELECT id, family, name, spec_hash, spec, sharpe_pp, created_ts FROM experiments WHERE kind IN ('backtest','wf_candidate') "
                             "AND status = 'ok' AND spec_hash != '' ORDER BY created_ts, rowid")
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            k = (r["family"], r["spec_hash"])
            if k in seen:
                continue
            try:
                spec = json.loads(r["spec"])
            except ValueError:
                continue
            seen[k] = {"id": r["id"], "family": r["family"], "name": r["name"], "spec_hash": r["spec_hash"], "spec": spec, "sharpe_pp": r["sharpe_pp"],
                       "features": self._feat(spec, r["spec_hash"])}
        return list(seen.values())

    def related_variants(self, family: str) -> dict[str, Any]:
        """Variants of OTHER families that are the same idea as a variant of ``family`` (mechanism score >= high): they count as trials of it."""
        sig = (family, self._exp_signature())
        hit = self._related.get(sig)
        if hit is not None:
            return hit
        rows = self._variant_rows()
        own = [r for r in rows if r["family"] == family]
        own_hashes = {r["spec_hash"] for r in own}
        related: dict[str, dict[str, Any]] = {}
        for c in rows:
            if c["family"] == family or c["spec_hash"] in own_hashes or c["spec_hash"] in related:
                continue
            for o in own:
                m = dup.mechanism_score(o["features"], c["features"])
                if m["score"] >= dup.HIGH_MECHANISM:
                    related[c["spec_hash"]] = {"experiment": c["id"], "family": c["family"], "name": c["name"], "spec_hash": c["spec_hash"],
                                               "mechanism_score": m["score"], "sharpe_pp": c["sharpe_pp"]}
                    break
        out = {"count": len(related), "items": list(related.values())}
        if len(self._related) > 500:
            self._related.clear()
        self._related[sig] = out
        return out

    def duplicate_check(self, spec: dict[str, Any], *, family: Optional[str] = None, exclude_id: Optional[str] = None, limit: int = 5) -> dict[str, Any]:
        """Duplicate risk of a spec against the experiments of other families: by mechanism and by description."""
        fam = family if family is not None else (spec.get("family") or spec.get("name", ""))
        feats = dup.spec_features(spec, self._symbol_of)
        cands = [c for c in self._variant_rows() if c["family"] != fam and c["id"] != exclude_id]
        res = dup.find_similar(feats, cands, embedder=self.embedder, limit=limit)
        res["compared_with"] = len(cands)
        return res

    # --------------------------------------------------------- experiments
    def family_stats(self, family: str) -> dict[str, Any]:
        rows = self.db.query("SELECT DISTINCT spec_hash, sharpe_pp FROM experiments WHERE family = ? AND kind IN ('backtest','wf_candidate') "
                             "AND status = 'ok' AND spec_hash != ''", (family,))
        seen: dict[str, Optional[float]] = {}
        for r in rows:
            seen.setdefault(r["spec_hash"], r["sharpe_pp"])
        total = self.db.one("SELECT COUNT(*) AS n FROM experiments WHERE family = ? AND kind IN ('backtest','wf_candidate')", (family,))["n"]
        n = len(seen)
        rel = self.related_variants(family) if n else {"count": 0, "items": []}
        trials = dict(seen)
        for item in rel["items"]:
            trials.setdefault(item["spec_hash"], item["sharpe_pp"])
        eff = n + rel["count"]
        return {"family": family, "variants": n, "runs": total, "trial_sharpes": [v for v in trials.values() if v is not None],
                "related_variants": rel["count"], "related": [{k: i[k] for k in ("experiment", "family", "name", "mechanism_score")} for i in rel["items"][:10]],
                "effective_variants": eff,
                "warning_level": "strong" if eff >= 20 else "moderate" if eff >= 5 else "none"}

    def _log(self, *, exp_id: str, kind: str, spec: dict[str, Any], status: str, error: str = "", summary: Optional[dict[str, Any]] = None,
             parent: Optional[str] = None, sharpe_pp: Optional[float] = None, n_obs: int = 0, as_of: str = "", snapshot_ids: Optional[list[str]] = None,
             strategy_id: Optional[str] = None) -> None:
        self.db.execute(
            "INSERT INTO experiments(id, created_ts, kind, family, strategy_id, name, spec_hash, spec, as_of, status, error, summary, snapshot_ids, parent_id, sharpe_pp, n_obs) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (exp_id, self.clock(), kind, spec.get("family") or spec.get("name", ""), strategy_id, spec.get("name", ""),
             spec_hash(spec) if spec.get("universe") and "entry" in spec else "", json.dumps(spec, default=str), as_of, status, error[:500],
             json.dumps(summary or {}, default=str), json.dumps(snapshot_ids or []), parent, sharpe_pp, n_obs))

    def _log_variant(self, family_parent: str, spec: dict[str, Any], info: dict[str, Any]) -> None:
        h = spec_hash(spec)
        if self.db.one("SELECT 1 FROM experiments WHERE family = ? AND spec_hash = ? AND kind IN ('backtest','wf_candidate') AND status = 'ok'",
                       (spec.get("family") or spec["name"], h)):
            return
        self._log(exp_id="wc_" + uuid.uuid4().hex[:8], kind="wf_candidate", spec=spec, status="ok", summary=info, parent=family_parent,
                  sharpe_pp=info.get("sharpe_pp"), n_obs=info.get("n_obs", 0), snapshot_ids=spec["universe"])

    def experiments(self, *, family: Optional[str] = None, kind: Optional[str] = None, status: Optional[str] = None, limit: int = 30,
                    cursor: Optional[int] = None) -> dict[str, Any]:
        where, params = [], []
        for col, v in (("family", family), ("kind", kind), ("status", status)):
            if v:
                where.append(f"{col} = ?")
                params.append(v)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.one(f"SELECT COUNT(*) AS n FROM experiments{clause}", params)["n"]
        offset = int(cursor or 0)
        rows = self.db.query(f"SELECT * FROM experiments{clause} ORDER BY created_ts DESC, rowid DESC LIMIT ? OFFSET ?", [*params, limit, offset])
        items = []
        fam_cache: dict[str, dict[str, Any]] = {}
        for r in rows:
            s = json.loads(r["summary"] or "{}")
            fam = fam_cache.setdefault(r["family"], self.family_stats(r["family"]))
            dupr = None
            if r["kind"] == "backtest" and r["status"] == "ok":
                dupr = s.get("duplicate")
                if dupr is None:
                    try:
                        dupr = self.duplicate_check(json.loads(r["spec"]), family=r["family"], exclude_id=r["id"])
                    except (ValueError, KeyError):
                        dupr = None
            items.append({"id": r["id"], "created_at": r["created_ts"], "kind": r["kind"], "family": r["family"], "name": r["name"], "spec_hash": r["spec_hash"],
                          "status": r["status"], "error": r["error"], "as_of": r["as_of"], "parent": r["parent_id"],
                          "ref": ArtifactRef("midas", "run", r["id"]).uri if r["kind"] == "backtest" else None,
                          "metrics": {k: (s.get("metrics") or s).get(k) for k in ("total_return", "sharpe", "max_drawdown", "trades")},
                          "variants_in_family": fam["variants"], "effective_variants": fam["effective_variants"], "warning_level": fam["warning_level"],
                          "duplicate_risk": dupr["level"] if dupr else None,
                          "duplicate_similar": [{k: x[k] for k in ("experiment", "name", "family", "level", "why")} for x in (dupr or {}).get("similar", [])[:3]]})
        fams = []
        for r in self.db.query("SELECT family FROM experiments GROUP BY family ORDER BY MAX(created_ts) DESC LIMIT 50"):
            f = self.family_stats(r["family"])
            fails = self.db.one("SELECT COUNT(*) AS n FROM experiments WHERE family = ? AND status IN ('failed','rejected')", (r["family"],))["n"]
            fams.append({"family": f["family"], "variants": f["variants"], "related_variants": f["related_variants"], "effective_variants": f["effective_variants"],
                         "runs": f["runs"], "failed_or_rejected": fails, "warning_level": f["warning_level"]})
        return {"experiments": items, "total": total, "next_cursor": offset + limit if offset + limit < total else None, "families": fams,
                "note": "Every variant ever run is kept, failures included; 'effective_variants' (variants plus near-duplicates under other names) is what a multiple-testing correction must account for."}

    # ------------------------------------------------------------------ run
    def _split(self, spec: dict[str, Any], as_of: Optional[str], fraction: Optional[float], split_date: Optional[str]) -> Optional[dict[str, Any]]:
        if fraction is None and not split_date:
            return None
        uni = prepare_universe(spec, self.load, as_of)
        idx = uni.index
        if split_date:
            start_i = int(idx.searchsorted(pd.Timestamp(split_date)))
        else:
            if not (0.1 <= float(fraction) <= 0.5):
                raise MidasError("invalid_request", f"holdout_fraction {fraction} is outside 0.1-0.5.", "Use e.g. 0.3 to seal the last 30% of bars.")
            start_i = int(len(idx) * (1.0 - float(fraction)))
        if start_i < 60 or len(idx) - start_i < 20:
            raise MidasError("no_data", f"The split leaves {start_i} dev bars and {len(idx) - start_i} holdout bars.", "Use a longer history or a different split.")
        return {"dev_end": idx[start_i - 1].strftime("%Y-%m-%d"), "holdout_start": idx[start_i].strftime("%Y-%m-%d"),
                "holdout_end": idx[-1].strftime("%Y-%m-%d"), "holdout_bars": int(len(idx) - start_i), "dev_bars": int(start_i), "status": "sealed"}

    def run(self, *, spec: Any = None, strategy: Optional[str] = None, as_of: Optional[str] = None, holdout_fraction: Optional[float] = None,
            split_date: Optional[str] = None, label: str = "") -> dict[str, Any]:
        strategy_id = None
        if strategy:
            st = self.strategy(strategy)
            spec, strategy_id = st["spec"], st["id"]
        if spec is None:
            raise MidasError("invalid_request", "backtest_run needs a spec or a strategy (id or name).", "Pass spec={...} or strategy='<name>'.")
        run_id = "bt_" + uuid.uuid4().hex[:8]
        check = self.validate(spec)
        if not check["ok"]:
            raw = spec if isinstance(spec, dict) else {}
            self._log(exp_id=run_id, kind="backtest", spec=raw, status="rejected", error=check["issues"][0]["message"], summary={"issues": check["issues"]},
                      strategy_id=strategy_id)
            raise MidasError("invalid_spec", "The strategy spec is not valid: " + check["issues"][0]["message"], check["issues"][0]["hint"],
                             issues=check["issues"], run_id=run_id, logged=True)
        n = check["normalized"]
        if label:
            n = {**n, "note": label[:500]}
        cutoff = as_of
        try:
            split = self._split(n, as_of, holdout_fraction, split_date)
            if split:
                cutoff = split["dev_end"]
            health = self._spec_health(n, cutoff)  # before any metric is computed
            out = run_backtest(n, self.load, as_of=cutoff)
        except MidasError as error:
            self._log(exp_id=run_id, kind="backtest", spec=n, status="failed", error=f"{error.code}: {error.message}", strategy_id=strategy_id, as_of=cutoff or "",
                      snapshot_ids=n["universe"])
            self.emit("midas.backtest.finished", {"run": run_id, "strategy": n["name"][:60], "ok": False})
            error.details["run_id"] = run_id
            error.details["logged"] = True
            raise
        except Exception as error:  # noqa: BLE001 — a crash is an experiment too
            self._log(exp_id=run_id, kind="backtest", spec=n, status="failed", error=f"{type(error).__name__}: {error}", strategy_id=strategy_id,
                      as_of=cutoff or "", snapshot_ids=n["universe"])
            self.emit("midas.backtest.finished", {"run": run_id, "strategy": n["name"][:60], "ok": False})
            raise
        effective_as_of = out.window["end"]
        summary = {"metrics": {k: out.metrics.get(k) for k in ("total_return", "cagr", "ann_vol", "sharpe", "max_drawdown", "trades", "exposure", "bars")},
                   "window": out.window, "benchmark_total_return": out.benchmark.get("total_return"), "holdout": split}
        self._log(exp_id=run_id, kind="backtest", spec=n, status="ok", summary=summary, sharpe_pp=out.metrics.get("sharpe_per_period"), n_obs=out.metrics["bars"],
                  as_of=effective_as_of, snapshot_ids=self._snapshot_ids(n), strategy_id=strategy_id)
        fam = self.family_stats(n["family"])
        mt = val.multiple_testing(out.metrics.get("sharpe_per_period"), out.metrics["bars"], fam["trial_sharpes"], fam["effective_variants"], skew=out.metrics.get("skew"),
                                  kurt=out.metrics.get("kurtosis"), ppy=out.universe.ppy, family=n["family"])
        dupres = self.duplicate_check(n, family=n["family"], exclude_id=run_id)
        doc = self._doc(run_id, n, out, fam, mt, split, health, dupres)
        run_dir = self._write_artifacts(run_id, n, out, doc)
        self.db.execute("UPDATE experiments SET summary = ? WHERE id = ?", (json.dumps({**summary, "multiple_testing": {k: mt.get(k) for k in ("variants_tried", "warning_level", "p_value_bonferroni", "haircut_sharpe_annualised")},
                                                                                        "duplicate": dupres, "data_health": {k: health["summary"].get(k) for k in ("status", "min_coverage")}}, default=str), run_id))
        report = self.run_report(run_id)
        self.emit("midas.backtest.finished", {"run": run_id, "strategy": n["name"][:60], "ok": True})
        step = max(1, len(out.equity) // 120)
        eq = out.equity.iloc[::step]
        if eq.index[-1] != out.equity.index[-1]:
            eq = pd.concat([eq, out.equity.iloc[[-1]]])
        return {"run_id": run_id, "ref": ArtifactRef("midas", "run", run_id).uri, "status": "ok", "strategy": n["name"], "spec_hash": spec_hash(n),
                "window": out.window, "metrics": out.metrics, "benchmark": out.benchmark,
                "variants": self._variants_doc(fam), "multiple_testing": mt, "data_health": health, "duplicate": dupres,
                "decision": self._decision_brief(report["decision"]), "evidence_quality": report["evidence_quality"],
                "holdout": split, "conversions": out.conversions, "warnings": out.warnings, "trades_total": len(out.trades), "trades": out.trades[:25],
                "equity": [{"date": ts.strftime("%Y-%m-%d"), "equity": round(float(r.equity), 2), "drawdown": round(float(r.drawdown), 4),
                            "benchmark": round(float(r.benchmark_equity), 2)} for ts, r in eq.iterrows()],
                "artifacts": str(run_dir), "disclaimer": reports_mod.DISCLAIMER}

    def _snapshot_ids(self, n: dict[str, Any]) -> list[str]:
        ids = list(n["universe"])
        for extra in [(n.get("benchmark") or {}).get("snapshot"), (n.get("rf") or {}).get("snapshot"), *(n.get("fx") or {}).values()]:
            if extra and extra not in ids:
                ids.append(extra)
        return ids

    # ------------------------------------------------------------ data health
    def _health_ids(self, spec: dict[str, Any]) -> list[str]:
        ids = list(spec["universe"])
        bench = (spec.get("benchmark") or {}).get("snapshot")
        if bench and bench not in ids:
            ids.append(bench)
        return ids

    def snapshot_health(self, snapshot_ids: list[str], *, start: Optional[str] = None, end: Optional[str] = None, common_window: bool = False) -> dict[str, Any]:
        """Health of each snapshot over [start, end]. With ``common_window`` and no explicit bounds, the window is the dates all of them share."""
        snaps = [self.load(i) for i in snapshot_ids]
        if common_window and (start is None or end is None) and snaps:
            firsts = [x.df.index[0] for x in snaps if len(x.df)]
            lasts = [x.df.index[-1] for x in snaps if len(x.df)]
            if firsts and lasts and max(firsts) <= min(lasts):
                start = start or max(firsts).strftime("%Y-%m-%d")
                end = end or min(lasts).strftime("%Y-%m-%d")
        series = [health_mod.snapshot_health(x, start=start, end=end) for x in snaps]
        return {"summary": health_mod.summarize(series), "series": series, "window": {"start": start, "end": end}}

    def _spec_health(self, spec: dict[str, Any], cutoff: Optional[str]) -> dict[str, Any]:
        try:
            return self.snapshot_health(self._health_ids(spec), end=cutoff, common_window=True)
        except MidasError:
            return {"summary": {"status": "unknown", "series": 0, "min_coverage": None, "issues": [], "note": "a snapshot could not be loaded"}, "series": [],
                    "window": {"start": None, "end": cutoff}}

    def data_health(self, *, snapshot_ids: Optional[list[str]] = None, run_id: Optional[str] = None, strategy: Optional[str] = None,
                    spec: Optional[dict[str, Any]] = None, start: Optional[str] = None, end: Optional[str] = None) -> dict[str, Any]:
        """Data health of snapshots, of the universe of a saved strategy or spec, or of the data a run used."""
        if run_id:
            row = self._exp(run_id)
            sp = json.loads(row["spec"])
            ids = self._health_ids(sp) if sp.get("universe") else []
            win = {}
            mfile = self.runs_dir / run_id / "metrics.json"
            if mfile.is_file():
                win = (json.loads(mfile.read_text(encoding="utf-8")).get("window") or {})
            res = self.snapshot_health(ids, start=start or win.get("data_start"), end=end or win.get("end") or row["as_of"] or None, common_window=not (start or win))
            res["run_id"] = run_id
        else:
            if strategy:
                spec = self.strategy(strategy)["spec"]
            if spec is not None:
                snapshot_ids = self._health_ids(spec)
            if not snapshot_ids:
                raise MidasError("invalid_request", "data_health needs snapshot_ids, run_id, strategy or spec.", "Pass e.g. snapshot_ids=['snp_...'] (see snapshots_list).")
            res = self.snapshot_health(list(snapshot_ids), start=start, end=end, common_window=start is None and end is None)
        res["note"] = "Computed on the raw frozen series before any metric. A null is reported as null, never filled or counted as 0."
        return res

    # ---------------------------------------------------------------- report
    def _validation_docs(self, run_id: str) -> list[dict[str, Any]]:
        out = []
        for r in self.db.query("SELECT id FROM experiments WHERE parent_id = ? AND kind = 'validation' AND status = 'ok' ORDER BY created_ts, rowid", (run_id,)):
            f = self.runs_dir / run_id / f"validation-{r['id']}.json"
            if f.is_file():
                try:
                    out.append(json.loads(f.read_text(encoding="utf-8")))
                except ValueError:
                    continue
        return out

    @staticmethod
    def _trade_row(t: dict[str, str]) -> dict[str, Any]:
        out: dict[str, Any] = dict(t)
        for k in ("entry_price", "exit_price", "weight", "return", "gross_return"):
            try:
                out[k] = float(t[k])
            except (KeyError, TypeError, ValueError):
                out[k] = None
        try:
            out["bars"] = int(float(t["bars"]))
        except (KeyError, TypeError, ValueError):
            out["bars"] = None
        out["open"] = str(t.get("open")).lower() == "true"
        return out

    def _assemble_doc(self, run_id: str) -> dict[str, Any]:
        row = self._exp(run_id)
        d = self.runs_dir / run_id
        if row["status"] != "ok" or not (d / "metrics.json").is_file():
            raise MidasError("invalid_request", f"Run {run_id} has status {row['status']}: {row['error'] or 'no report'}.", "Only finished backtests have a report.")
        doc: dict[str, Any] = {"run_id": run_id, "ref": ArtifactRef("midas", "run", run_id).uri, "spec": json.loads(row["spec"]), "spec_hash": row["spec_hash"],
                               "generated_at": datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        doc.update(json.loads((d / "metrics.json").read_text(encoding="utf-8")))
        doc["snapshots"] = json.loads((d / "snapshots.json").read_text(encoding="utf-8")) if (d / "snapshots.json").is_file() else []
        if (d / "trades.csv").is_file():
            with open(d / "trades.csv", encoding="utf-8", newline="") as fh:
                doc["trades"] = [self._trade_row(t) for t in csv.DictReader(fh)]
        if not doc.get("data_health"):  # runs made before the gate existed: computed now, on the same window
            win = doc.get("window") or {}
            doc["data_health"] = self.snapshot_health(self._health_ids(doc["spec"]), start=win.get("data_start"), end=win.get("end"))
        if not doc.get("duplicate"):
            doc["duplicate"] = self.duplicate_check(doc["spec"], family=row["family"], exclude_id=run_id)
        # the variants block follows the log as it is now (effective variants include near-duplicates registered since)
        fam = self.family_stats(row["family"])
        doc["variants"] = self._variants_doc(fam)
        doc["validations"] = self._validation_docs(run_id)
        return doc

    def run_report(self, run_id: str) -> dict[str, Any]:
        """The lab report contract of a finished backtest (JSON): eight sections, tagged claims and a rule-based decision."""
        return labreport.build_report(self._assemble_doc(run_id))

    def _report_markdown(self, doc: dict[str, Any]) -> str:
        return labreport.to_markdown(labreport.build_report(doc), appendix=reports_mod.run_appendix(doc))

    def run_report_text(self, run_id: str, fmt: str = "md") -> str:
        doc = self._assemble_doc(run_id)
        rep = labreport.build_report(doc)
        if fmt == "html":
            return labreport.to_html(rep, "\n".join(reports_mod.run_appendix(doc)))
        return labreport.to_markdown(rep, appendix=reports_mod.run_appendix(doc))

    def _refresh_report_file(self, run_id: str) -> None:
        try:
            write_text_atomic(self.runs_dir / run_id / "report.md", self.run_report_text(run_id, "md"))
        except MidasError:
            pass

    # ------------------------------------------------------------- duplicates
    def duplicates(self, *, run_id: Optional[str] = None, spec: Optional[dict[str, Any]] = None, strategy: Optional[str] = None,
                   min_level: str = "medium", limit: int = 20) -> dict[str, Any]:
        """Duplicate risk of one run, of a spec not yet run, or (with nothing given) of every backtest in the log."""
        if run_id:
            row = self._exp(run_id)
            res = self.duplicate_check(json.loads(row["spec"]), family=row["family"], exclude_id=run_id)
            return {"run_id": run_id, "family": row["family"], **res}
        if strategy:
            spec = self.strategy(strategy)["spec"]
        if spec is not None:
            check = self.validate(spec)
            if not check["ok"]:
                raise MidasError("invalid_spec", "The strategy spec is not valid: " + check["issues"][0]["message"], check["issues"][0]["hint"], issues=check["issues"])
            n = check["normalized"]
            return {"family": n["family"], "hypothetical": True, **self.duplicate_check(n, family=n["family"])}
        floor = dup.LEVELS.index(min_level)
        found = []
        for r in self._variant_rows():
            res = self.duplicate_check(r["spec"], family=r["family"], exclude_id=r["id"])
            if dup.LEVELS.index(res["level"]) >= floor:
                found.append({"experiment": r["id"], "name": r["name"], "family": r["family"], "level": res["level"], "similar": res["similar"][:3]})
        found.sort(key=lambda x: (-dup.LEVELS.index(x["level"]), x["family"]))
        return {"min_level": min_level, "experiments": found[:limit], "total": len(found), "compared": len(self._variant_rows()),
                "method": dup.METHOD,
                "note": "Risk is by mechanism (indicator kinds, rule leaves, universe) and by description; it never blocks, it is shown in reports and counted in the multiple-testing correction."}

    @staticmethod
    def _variants_doc(fam: dict[str, Any]) -> dict[str, Any]:
        return {"family": fam["family"], "tried": fam["variants"], "related": fam["related_variants"], "effective": fam["effective_variants"],
                "warning_level": fam["warning_level"]}

    @staticmethod
    def _decision_brief(d: dict[str, Any]) -> dict[str, Any]:
        return {"decision": d["decision"], "label": d["label"], "because": d["because"], "reasons": [r["text"] for r in d["reasons"][:4]], "needs": d["needs"]}

    def _doc(self, run_id: str, n: dict[str, Any], out: BacktestOutput, fam: dict[str, Any], mt: dict[str, Any], split: Optional[dict[str, Any]],
             health: Optional[dict[str, Any]] = None, dupres: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return {"run_id": run_id, "ref": ArtifactRef("midas", "run", run_id).uri, "spec": n, "spec_hash": spec_hash(n), "window": out.window,
                "metrics": out.metrics, "benchmark": out.benchmark, "variants": self._variants_doc(fam), "data_health": health, "duplicate": dupres,
                "multiple_testing": mt, "holdout": split, "conversions": out.conversions, "warnings": out.warnings,
                "snapshots": [provenance(self.store.meta(s)) for s in self._snapshot_ids(n)], "trades": out.trades,
                "generated_at": datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def _write_artifacts(self, run_id: str, n: dict[str, Any], out: BacktestOutput, doc: dict[str, Any]) -> Path:
        d = self.runs_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        write_json_atomic(d / "spec.json", n, sort_keys=True)
        write_json_atomic(d / "snapshots.json", doc["snapshots"])
        write_json_atomic(d / "metrics.json", _plain({k: doc[k] for k in ("metrics", "benchmark", "window", "variants", "multiple_testing", "holdout", "conversions", "warnings", "data_health", "duplicate")}))
        out.equity.to_csv(d / "equity.csv", index_label="date", float_format="%.8g")
        with open(d / "trades.csv", "w", encoding="utf-8", newline="") as fh:
            cols = ["asset", "snapshot", "side", "entry_date", "entry_price", "exit_date", "exit_price", "weight", "bars", "return", "gross_return", "open"]
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows(out.trades)
        write_text_atomic(d / "report.md", self._report_markdown({**doc, "validations": []}))
        return d

    # ------------------------------------------------------------- read run
    def _exp(self, run_id: str):
        row = self.db.one("SELECT * FROM experiments WHERE id = ?", (run_id,))
        if row is None:
            raise MidasError("not_found", f"Unknown run '{run_id}'.", "List runs with experiments_list.")
        return row

    def run_get(self, run_id: str, *, equity_limit: int = 240, trades_limit: int = 50, cursor: Optional[int] = None) -> dict[str, Any]:
        row = self._exp(run_id)
        d = self.runs_dir / run_id
        out: dict[str, Any] = {"run_id": run_id, "ref": ArtifactRef("midas", "run", run_id).uri, "status": row["status"], "error": row["error"],
                               "kind": row["kind"], "family": row["family"], "spec": json.loads(row["spec"]), "created_at": row["created_ts"]}
        if row["status"] != "ok" or not (d / "metrics.json").is_file():
            out["summary"] = json.loads(row["summary"] or "{}")
            return out
        out.update(json.loads((d / "metrics.json").read_text(encoding="utf-8")))
        eq = pd.read_csv(d / "equity.csv", index_col=0)
        step = max(1, len(eq) // max(1, equity_limit))
        sampled = eq.iloc[::step]
        if sampled.index[-1] != eq.index[-1]:
            sampled = pd.concat([sampled, eq.iloc[[-1]]])
        out["equity"] = [{"date": str(i), **{k: (None if pd.isna(v) else round(float(v), 4)) for k, v in r.items()}} for i, r in sampled.iterrows()]
        with open(d / "trades.csv", encoding="utf-8", newline="") as fh:
            trades = list(csv.DictReader(fh))
        start = int(cursor or 0)
        out["trades_total"] = len(trades)
        out["trades"] = trades[start:start + trades_limit]
        out["next_cursor"] = start + trades_limit if start + trades_limit < len(trades) else None
        out["snapshots"] = json.loads((d / "snapshots.json").read_text(encoding="utf-8"))
        out["validations"] = [{"id": r["id"], "created_at": r["created_ts"], "status": r["status"], "summary": json.loads(r["summary"] or "{}")}
                              for r in self.db.query("SELECT * FROM experiments WHERE parent_id = ? AND kind IN ('validation','holdout') ORDER BY created_ts DESC", (run_id,))]
        try:
            rep = self.run_report(run_id)
            out["decision"] = self._decision_brief(rep["decision"])
            out["evidence_quality"] = rep["evidence_quality"]
            out["data_health"] = out.get("data_health") or rep["data_health"]
            out["duplicate"] = rep["duplicate"]
        except MidasError:
            pass
        out["disclaimer"] = reports_mod.DISCLAIMER
        return out

    # ------------------------------------------------------------ validate
    def validate_run(self, run_id: str, *, methods: Optional[list[str]] = None, windows: int = 5, grid: Optional[dict[str, list[Any]]] = None,
                     n: int = 1000, block: Optional[int] = None, seed: int = 0, reveal_holdout: bool = False) -> dict[str, Any]:
        row = self._exp(run_id)
        if row["kind"] != "backtest" or row["status"] != "ok":
            raise MidasError("invalid_request", f"Run {run_id} is a {row['kind']} with status {row['status']}: only finished backtests can be validated.",
                             "Use the run_id of a backtest_run that returned status ok.")
        methods = list(methods or METHODS)
        bad = [m for m in methods if m not in METHODS]
        if bad:
            raise MidasError("invalid_request", f"Unknown validation method(s): {', '.join(bad)}.", f"Use: {', '.join(METHODS)}. The holdout is opened with reveal_holdout=true.")
        spec = json.loads(row["spec"])
        summary = json.loads(row["summary"] or "{}")
        split = summary.get("holdout")
        cutoff = (split or {}).get("dev_end") or row["as_of"]
        vid = "va_" + uuid.uuid4().hex[:8]
        results: dict[str, Any] = {}
        try:
            base = run_backtest(spec, self.load, as_of=cutoff)
            fam = self.family_stats(spec["family"])
            for m in methods:
                try:
                    if m == "walk_forward":
                        results[m] = val.walk_forward(spec, self.load, as_of=cutoff, windows=windows, grid=grid,
                                                      log_variant=lambda s, info: self._log_variant(run_id, s, info))
                        fam = self.family_stats(spec["family"])
                    elif m == "permutation":
                        results[m] = val.permutation_test(base, n=n, block=block or 20, seed=seed)
                    elif m == "bootstrap":
                        results[m] = val.bootstrap_sharpe(base, n=n, block=block or 10, seed=seed)
                    elif m == "multiple_testing":
                        pass
                except MidasError as error:
                    results[m] = {"error": error.message, "code": error.code, "hint": error.hint}
            if "multiple_testing" in methods:
                fam = self.family_stats(spec["family"])
                results["multiple_testing"] = val.multiple_testing(base.metrics.get("sharpe_per_period"), base.metrics["bars"], fam["trial_sharpes"], fam["effective_variants"],
                                                                   skew=base.metrics.get("skew"), kurt=base.metrics.get("kurtosis"), ppy=base.universe.ppy, family=spec["family"])
            holdout_info: dict[str, Any]
            if reveal_holdout:
                if not split:
                    raise MidasError("invalid_request", f"Run {run_id} has no sealed holdout.", "Re-run with holdout_fraction=0.3 (or split_date) so a holdout is sealed first.")
                peeks = self.db.one("SELECT COUNT(*) AS n FROM experiments WHERE parent_id = ? AND kind = 'holdout'", (run_id,))["n"] + 1
                ho = run_backtest(spec, self.load, as_of=split["holdout_end"], eval_start=split["holdout_start"])
                holdout_info = {"status": "opened", "peek_number": peeks, "window": ho.window, "metrics": {k: ho.metrics.get(k) for k in (
                    "total_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "trades", "hit_rate", "exposure", "bars")},
                    "benchmark": ho.benchmark, "dev_metrics": {k: base.metrics.get(k) for k in ("total_return", "cagr", "ann_vol", "sharpe", "max_drawdown", "trades")},
                    "note": "The spec is exactly the one validated on dev data; nothing was changed after seeing this." if peeks == 1 else
                            f"This is look number {peeks} at the holdout: it is no longer untouched, treat its result as a development set."}
                self._log(exp_id="ho_" + uuid.uuid4().hex[:8], kind="holdout", spec=spec, status="ok", parent=run_id, summary=holdout_info, as_of=split["holdout_end"],
                          sharpe_pp=ho.metrics.get("sharpe_per_period"), n_obs=ho.metrics["bars"], snapshot_ids=self._snapshot_ids(spec))
                results["holdout"] = holdout_info
            else:
                results["holdout"] = {"status": "sealed" if split else "none", **({"holdout_start": split["holdout_start"], "holdout_bars": split["holdout_bars"],
                                                                                 "note": "Untouched. Ask with reveal_holdout=true when the spec is final."} if split else {"note": "This run had no holdout; re-run with holdout_fraction."})}
        except MidasError as error:
            self._log(exp_id=vid, kind="validation", spec=spec, status="failed", error=f"{error.code}: {error.message}", parent=run_id, as_of=cutoff)
            error.details["validation_id"] = vid
            raise
        headline = {"methods": methods, "permutation_p": (results.get("permutation") or {}).get("p_value"),
                    "bootstrap_ci": [(results.get("bootstrap") or {}).get("ci_low"), (results.get("bootstrap") or {}).get("ci_high")] if "bootstrap" in results else None,
                    "wf_positive_windows": (results.get("walk_forward") or {}).get("positive_windows"),
                    "holdout": results["holdout"].get("status")}
        self._log(exp_id=vid, kind="validation", spec=spec, status="ok", summary=headline, parent=run_id, as_of=cutoff, n_obs=base.metrics["bars"])
        doc = {"validation_id": vid, "run_id": run_id, "as_of": cutoff, "seed": seed, "results": results,
               "variants": {"family": spec["family"], "tried": self.family_stats(spec["family"])["variants"], "effective": self.family_stats(spec["family"])["effective_variants"]},
               "disclaimer": reports_mod.DISCLAIMER}
        d = self.runs_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        write_json_atomic(d / f"validation-{vid}.json", _plain(doc))
        self._refresh_report_file(run_id)
        brief = self._decision_brief(self.run_report(run_id)["decision"])
        doc["decision"] = brief
        return doc
