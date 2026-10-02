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

from . import reports as reports_mod
from . import validation as val
from .backtest import BacktestOutput, prepare_universe, run_backtest
from .db import Database
from .errors import MidasError
from .hoard_link.atomic import write_json_atomic, write_text_atomic
from .hoard_link.artifacts import ArtifactRef
from .snapshots import Snapshot, SnapshotStore, provenance
from .strategy import spec_hash, validate_spec

METHODS = ("walk_forward", "permutation", "bootstrap", "multiple_testing")


def _plain(doc: Any) -> Any:
    """The document with anything JSON cannot hold (numpy numbers, timestamps) written as text, as before."""
    return json.loads(json.dumps(doc, default=str))


class Lab:
    def __init__(self, db: Database, store: SnapshotStore, runs_dir: Path, *, emit: Callable[[str, dict[str, Any]], None] = lambda t, d: None,
                 clock: Callable[[], float] = time.time):
        self.db, self.store, self.runs_dir, self.emit, self.clock = db, store, Path(runs_dir), emit, clock
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Snapshot] = {}

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
                        "universe": spec["universe"], "variants_tried": fam["variants"], "spec": spec})
        return out

    def delete_strategy(self, ident: str) -> dict[str, Any]:
        s = self.strategy(ident)
        self.db.execute("DELETE FROM strategies WHERE id = ?", (s["id"],))
        return {"deleted": s["id"], "name": s["name"], "note": "Experiments run with it stay in the log."}

    # --------------------------------------------------------- experiments
    def family_stats(self, family: str) -> dict[str, Any]:
        rows = self.db.query("SELECT DISTINCT spec_hash, sharpe_pp FROM experiments WHERE family = ? AND kind IN ('backtest','wf_candidate') "
                             "AND status = 'ok' AND spec_hash != ''", (family,))
        seen: dict[str, Optional[float]] = {}
        for r in rows:
            seen.setdefault(r["spec_hash"], r["sharpe_pp"])
        total = self.db.one("SELECT COUNT(*) AS n FROM experiments WHERE family = ? AND kind IN ('backtest','wf_candidate')", (family,))["n"]
        n = len(seen)
        return {"family": family, "variants": n, "runs": total, "trial_sharpes": [v for v in seen.values() if v is not None],
                "warning_level": "strong" if n >= 20 else "moderate" if n >= 5 else "none"}

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
            items.append({"id": r["id"], "created_at": r["created_ts"], "kind": r["kind"], "family": r["family"], "name": r["name"], "spec_hash": r["spec_hash"],
                          "status": r["status"], "error": r["error"], "as_of": r["as_of"], "parent": r["parent_id"],
                          "ref": ArtifactRef("midas", "run", r["id"]).uri if r["kind"] == "backtest" else None,
                          "metrics": {k: (s.get("metrics") or s).get(k) for k in ("total_return", "sharpe", "max_drawdown", "trades")},
                          "variants_in_family": fam["variants"], "warning_level": fam["warning_level"]})
        fams = []
        for r in self.db.query("SELECT family FROM experiments GROUP BY family ORDER BY MAX(created_ts) DESC LIMIT 50"):
            f = self.family_stats(r["family"])
            fails = self.db.one("SELECT COUNT(*) AS n FROM experiments WHERE family = ? AND status IN ('failed','rejected')", (r["family"],))["n"]
            fams.append({"family": f["family"], "variants": f["variants"], "runs": f["runs"], "failed_or_rejected": fails, "warning_level": f["warning_level"]})
        return {"experiments": items, "total": total, "next_cursor": offset + limit if offset + limit < total else None, "families": fams,
                "note": "Every variant ever run is kept, failures included; 'variants' is what a multiple-testing correction must account for."}

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
        mt = val.multiple_testing(out.metrics.get("sharpe_per_period"), out.metrics["bars"], fam["trial_sharpes"], fam["variants"], skew=out.metrics.get("skew"),
                                  kurt=out.metrics.get("kurtosis"), ppy=out.universe.ppy, family=n["family"])
        doc = self._doc(run_id, n, out, fam, mt, split)
        run_dir = self._write_artifacts(run_id, n, out, doc)
        self.db.execute("UPDATE experiments SET summary = ? WHERE id = ?", (json.dumps({**summary, "multiple_testing": {k: mt.get(k) for k in ("variants_tried", "warning_level", "p_value_bonferroni", "haircut_sharpe_annualised")}}, default=str), run_id))
        self.emit("midas.backtest.finished", {"run": run_id, "strategy": n["name"][:60], "ok": True})
        step = max(1, len(out.equity) // 120)
        eq = out.equity.iloc[::step]
        if eq.index[-1] != out.equity.index[-1]:
            eq = pd.concat([eq, out.equity.iloc[[-1]]])
        return {"run_id": run_id, "ref": ArtifactRef("midas", "run", run_id).uri, "status": "ok", "strategy": n["name"], "spec_hash": spec_hash(n),
                "window": out.window, "metrics": out.metrics, "benchmark": out.benchmark,
                "variants": {"family": fam["family"], "tried": fam["variants"], "warning_level": fam["warning_level"]}, "multiple_testing": mt,
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

    def _doc(self, run_id: str, n: dict[str, Any], out: BacktestOutput, fam: dict[str, Any], mt: dict[str, Any], split: Optional[dict[str, Any]]) -> dict[str, Any]:
        return {"run_id": run_id, "ref": ArtifactRef("midas", "run", run_id).uri, "spec": n, "spec_hash": spec_hash(n), "window": out.window,
                "metrics": out.metrics, "benchmark": out.benchmark, "variants": {"family": fam["family"], "tried": fam["variants"], "warning_level": fam["warning_level"]},
                "multiple_testing": mt, "holdout": split, "conversions": out.conversions, "warnings": out.warnings,
                "snapshots": [provenance(self.store.meta(s)) for s in self._snapshot_ids(n)], "trades": out.trades,
                "generated_at": datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def _write_artifacts(self, run_id: str, n: dict[str, Any], out: BacktestOutput, doc: dict[str, Any]) -> Path:
        d = self.runs_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        write_json_atomic(d / "spec.json", n, sort_keys=True)
        write_json_atomic(d / "snapshots.json", doc["snapshots"])
        write_json_atomic(d / "metrics.json", _plain({k: doc[k] for k in ("metrics", "benchmark", "window", "variants", "multiple_testing", "holdout", "conversions", "warnings")}))
        out.equity.to_csv(d / "equity.csv", index_label="date", float_format="%.8g")
        with open(d / "trades.csv", "w", encoding="utf-8", newline="") as fh:
            cols = ["asset", "snapshot", "side", "entry_date", "entry_price", "exit_date", "exit_price", "weight", "bars", "return", "gross_return", "open"]
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows(out.trades)
        write_text_atomic(d / "report.md", reports_mod.run_markdown(doc))
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
                results["multiple_testing"] = val.multiple_testing(base.metrics.get("sharpe_per_period"), base.metrics["bars"], fam["trial_sharpes"], fam["variants"],
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
               "variants": {"family": spec["family"], "tried": self.family_stats(spec["family"])["variants"]}, "disclaimer": reports_mod.DISCLAIMER}
        d = self.runs_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        write_json_atomic(d / f"validation-{vid}.json", _plain(doc))
        return doc
