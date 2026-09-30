"""Investment theses: a claim, evidence for and against, a forced rival hypothesis and machine-checkable invalidation rules.

A thesis carries an ``as_of`` cutoff — the moment its claim and evidence describe. Evidence metrics are computed on
data sliced at that cutoff (nothing later may leak in). Checks are monitoring: they evaluate the rules at a check
date ``as_of >= thesis.as_of`` (default today) on the snapshots available then, again sliced at that date.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd

from . import rules as rules_mod
from .db import Database
from .errors import MidasError
from .hoard_link.artifacts import ArtifactRef, parse_ref
from .snapshots import Snapshot, SnapshotStore, provenance

STATUSES = ("open", "confirmed", "invalidated", "expired", "archived")
EVIDENCE_KINDS = ("snapshot_metric", "url", "note", "family_ref")
HORIZON = re.compile(r"^(\d{1,3})([dwmy])$", re.I)
STALE_DAYS = {"D": 10, "W": 21, "M": 75, "Q": 150, "A": 400}


def iso_date(value: Any, name: str) -> str:
    try:
        return pd.Timestamp(str(value)).strftime("%Y-%m-%d")
    except (ValueError, TypeError) as error:
        raise MidasError("invalid_request", f"'{name}' is not a date: {value!r}.", "Use YYYY-MM-DD.") from error


def horizon_end(as_of: str, horizon: str) -> Optional[str]:
    """The date a horizon like '12m' / '2y' / '2027-06-30' ends, or None when it is open-ended."""
    if not horizon:
        return None
    m = HORIZON.match(horizon.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        off = {"d": pd.DateOffset(days=n), "w": pd.DateOffset(weeks=n), "m": pd.DateOffset(months=n), "y": pd.DateOffset(years=n)}[unit]
        return (pd.Timestamp(as_of) + off).strftime("%Y-%m-%d")
    try:
        return pd.Timestamp(horizon).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


class Theses:
    def __init__(self, db: Database, store: SnapshotStore, *, fetch: Callable[..., tuple[dict[str, Any], bool]],
                 catalogue: Callable[[str], Optional[dict[str, Any]]], emit: Callable[[str, dict[str, Any]], None] = lambda t, d: None,
                 clock: Callable[[], float] = time.time):
        self.db = db
        self.store = store
        self._fetch = fetch          # (provider, symbol, start, end, interval, options) -> (snapshot meta, created)
        self._catalogue = catalogue  # symbol -> {"provider":..., "symbol":...} or None
        self.emit = emit
        self.clock = clock

    def today(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ helpers
    def _row(self, thesis_id: str):
        row = self.db.one("SELECT * FROM theses WHERE id = ?", (thesis_id,))
        if row is None:
            raise MidasError("not_found", f"Unknown thesis '{thesis_id}'.", "List theses with thesis_list.")
        return row

    @staticmethod
    def ref(thesis_id: str) -> str:
        return ArtifactRef("midas", "thesis", thesis_id).uri

    def evidence(self, thesis_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM thesis_evidence WHERE thesis_id = ? ORDER BY added_ts, rowid", (thesis_id,))
        return [{"id": r["id"], "n": i + 1, "label": f"E{i + 1}", "side": r["side"], "kind": r["kind"], "ref": r["ref"], "title": r["title"],
                 "quote": r["quote"], "metric": json.loads(r["metric"] or "{}")} for i, r in enumerate(rows)]

    def _last_check(self, thesis_id: str) -> Optional[dict[str, Any]]:
        row = self.db.one("SELECT * FROM thesis_checks WHERE thesis_id = ? ORDER BY ts DESC, rowid DESC LIMIT 1", (thesis_id,))
        return self._check_dict(row) if row else None

    @staticmethod
    def _check_dict(r) -> dict[str, Any]:
        return {"id": r["id"], "thesis_id": r["thesis_id"], "ts": r["ts"], "as_of": r["as_of"], "results": json.loads(r["results"] or "[]"),
                "tripped": bool(r["tripped"]), "status_before": r["status_before"], "status_after": r["status_after"],
                "snapshots": json.loads(r["snapshots"] or "[]"), "warnings": json.loads(r["warnings"] or "[]"), "signature": r["signature"]}

    def _dict(self, row, *, full: bool = True) -> dict[str, Any]:
        evidence = self.evidence(row["id"])
        out = {
            "id": row["id"], "ref": self.ref(row["id"]), "title": row["title"], "claim": row["claim"], "status": row["status"],
            "as_of": row["as_of"], "horizon": row["horizon"], "horizon_end": horizon_end(row["as_of"], row["horizon"]),
            "assets": json.loads(row["assets"] or "[]"), "assumptions": json.loads(row["assumptions"] or "[]"),
            "rival": row["rival"], "rules": json.loads(row["rules"] or "[]"), "notes": row["notes"],
            "created_at": row["created_ts"], "updated_at": row["updated_ts"],
            "evidence_counts": {"for": sum(1 for e in evidence if e["side"] == "for"), "against": sum(1 for e in evidence if e["side"] == "against")},
        }
        if full:
            out["evidence"] = evidence
            out["last_check"] = self._last_check(row["id"])
        return out

    # ------------------------------------------------------------ validation
    def _clean_assets(self, assets: Any) -> list[dict[str, Any]]:
        out = []
        for a in assets or []:
            if isinstance(a, str):
                a = {"symbol": a}
            if not isinstance(a, dict) or not (a.get("symbol") or a.get("snapshot_id")):
                raise MidasError("invalid_request", f"Each asset needs a symbol or snapshot_id, got {a!r}.",
                                 "Example: {\"symbol\": \"aapl.us\", \"provider\": \"stooq\"}.")
            item = {k: a[k] for k in ("symbol", "provider", "snapshot_id", "alias", "currency") if a.get(k)}
            if item.get("snapshot_id"):
                meta = self.store.meta(item["snapshot_id"])
                item.setdefault("symbol", meta["symbol"])
                item.setdefault("provider", meta["provider"])
            out.append(item)
        if len(out) > 30:
            raise MidasError("invalid_request", "A thesis can list at most 30 assets.", "Split it into several theses.")
        return out

    def _clean_rules(self, rules: Any, previous: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
        prev_by_expr = {r["expr"]: r for r in (previous or [])}
        out: list[dict[str, Any]] = []
        for idx, r in enumerate(rules or []):
            if isinstance(r, str):
                r = {"expr": r}
            expr = str((r or {}).get("expr", "")).strip()
            check = rules_mod.validate(expr)
            if not check["ok"]:
                raise MidasError("bad_rule", f"Rule {idx + 1} is not valid: {check['issues'][0]['message']}", check["issues"][0].get("hint", ""),
                                 issues=check["issues"], rule_index=idx)
            old = prev_by_expr.get(expr)
            out.append({"id": f"r{idx + 1}", "expr": expr, "description": str(r.get("description", ""))[:300],
                        "symbols": check["symbols"], "last": (old or {}).get("last")})
        if len(out) > 20:
            raise MidasError("invalid_request", "At most 20 rules per thesis.", "Combine conditions with and/or.")
        return out

    @staticmethod
    def _clean_assumptions(items: Any) -> list[str]:
        out = [str(x).strip()[:300] for x in (items or []) if str(x).strip()]
        if len(out) > 20:
            raise MidasError("invalid_request", "At most 20 assumptions.", "Keep the ones that would change the conclusion.")
        return out

    # ---------------------------------------------------------------- CRUD
    def create(self, *, title: str, claim: str, rival: str, as_of: Optional[str] = None, horizon: str = "", assets: Any = None,
               assumptions: Any = None, rules: Any = None, notes: str = "") -> dict[str, Any]:
        title, claim, rival = title.strip(), claim.strip(), rival.strip()
        if len(title) < 3 or not claim:
            raise MidasError("invalid_request", "A thesis needs a title and a claim.", "State the claim in one or two falsifiable sentences.")
        if len(rival) < 12:
            raise MidasError("rival_required", "A thesis needs a rival hypothesis: the best alternative explanation of the same facts.",
                             "Write what would be true if the claim is wrong (at least a sentence), e.g. 'The rally is a liquidity effect, not earnings growth.'")
        as_of_date = iso_date(as_of or self.today(), "as_of")
        if as_of_date > self.today():
            raise MidasError("invalid_request", f"as_of {as_of_date} is in the future.", "as_of is the knowledge cutoff: today or earlier.")
        hz = (horizon or "").strip()
        if hz and not HORIZON.match(hz) and horizon_end(as_of_date, hz) is None:
            raise MidasError("invalid_request", f"Horizon '{hz}' is not understood.", "Use 6m, 12m, 2y or a date (YYYY-MM-DD).")
        thesis_id = "th_" + uuid.uuid4().hex[:8]
        now = self.clock()
        self.db.execute(
            "INSERT INTO theses(id, title, claim, assets, as_of, horizon, status, assumptions, rival, rules, notes, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (thesis_id, title[:200], claim[:2000], json.dumps(self._clean_assets(assets)), as_of_date, hz, "open",
             json.dumps(self._clean_assumptions(assumptions)), rival[:2000], json.dumps(self._clean_rules(rules)), notes[:4000], now, now))
        self.emit("midas.thesis.created", {"thesis": thesis_id, "title": title[:80]})
        return self.get(thesis_id)

    def get(self, thesis_id: str) -> dict[str, Any]:
        return self._dict(self._row(thesis_id))

    def list(self, *, status: Optional[str] = None, query: Optional[str] = None, limit: int = 30, cursor: Optional[int] = None) -> dict[str, Any]:
        where, params = [], []
        if status:
            if status not in STATUSES:
                raise MidasError("invalid_request", f"Unknown status '{status}'.", f"Use one of: {', '.join(STATUSES)}.")
            where.append("status = ?")
            params.append(status)
        if query:
            where.append("(lower(title) LIKE ? OR lower(claim) LIKE ?)")
            params.extend([f"%{query.lower()}%"] * 2)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.one(f"SELECT COUNT(*) AS n FROM theses{clause}", params)["n"]
        offset = int(cursor or 0)
        rows = self.db.query(f"SELECT * FROM theses{clause} ORDER BY updated_ts DESC, rowid DESC LIMIT ? OFFSET ?", [*params, limit, offset])
        items = []
        for r in rows:
            d = self._dict(r, full=False)
            states = [(x.get("last") or {}).get("state", "unchecked") for x in d["rules"]]
            items.append({k: d[k] for k in ("id", "ref", "title", "status", "as_of", "horizon", "evidence_counts", "updated_at")} |
                         {"claim": d["claim"][:200], "rules": len(d["rules"]), "rule_states": states,
                          "assets": [a.get("symbol") for a in d["assets"]]})
        return {"theses": items, "total": total, "next_cursor": offset + limit if offset + limit < total else None}

    def update(self, thesis_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        row = self._row(thesis_id)
        sets, params = [], []
        changed: list[str] = []

        def put(col: str, val: Any, name: Optional[str] = None) -> None:
            sets.append(f"{col} = ?")
            params.append(val)
            changed.append(name or col)

        if patch.get("title") is not None:
            if len(patch["title"].strip()) < 3:
                raise MidasError("invalid_request", "Title too short.", "Use at least 3 characters.")
            put("title", patch["title"].strip()[:200])
        if patch.get("claim") is not None:
            put("claim", patch["claim"].strip()[:2000])
        if patch.get("rival") is not None:
            if len(patch["rival"].strip()) < 12:
                raise MidasError("rival_required", "The rival hypothesis cannot be emptied.", "Replace it with a better alternative instead of deleting it.")
            put("rival", patch["rival"].strip()[:2000])
        if patch.get("horizon") is not None:
            hz = patch["horizon"].strip()
            if hz and not HORIZON.match(hz) and horizon_end(row["as_of"], hz) is None:
                raise MidasError("invalid_request", f"Horizon '{hz}' is not understood.", "Use 6m, 12m, 2y or a date (YYYY-MM-DD).")
            put("horizon", hz)
        if patch.get("status") is not None:
            if patch["status"] not in STATUSES:
                raise MidasError("invalid_request", f"Unknown status '{patch['status']}'.", f"Use one of: {', '.join(STATUSES)}.")
            put("status", patch["status"])
        if patch.get("assets") is not None:
            put("assets", json.dumps(self._clean_assets(patch["assets"])), "assets")
        if patch.get("assumptions") is not None:
            put("assumptions", json.dumps(self._clean_assumptions(patch["assumptions"])), "assumptions")
        if patch.get("rules") is not None:
            put("rules", json.dumps(self._clean_rules(patch["rules"], json.loads(row["rules"] or "[]"))), "rules")
        if patch.get("notes") is not None:
            put("notes", patch["notes"][:4000])
        if not sets:
            return self.get(thesis_id) | {"changed": []}
        sets.append("updated_ts = ?")
        params.extend([self.clock(), thesis_id])
        self.db.execute(f"UPDATE theses SET {', '.join(sets)} WHERE id = ?", params)
        return self.get(thesis_id) | {"changed": changed}

    def delete(self, thesis_id: str) -> dict[str, Any]:
        row = self._row(thesis_id)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM thesis_evidence WHERE thesis_id = ?", (thesis_id,))
            conn.execute("DELETE FROM thesis_checks WHERE thesis_id = ?", (thesis_id,))
            conn.execute("DELETE FROM committee_runs WHERE thesis_id = ?", (thesis_id,))
            conn.execute("DELETE FROM theses WHERE id = ?", (thesis_id,))
        return {"deleted": thesis_id, "title": row["title"]}

    # -------------------------------------------------------------- evidence
    def point_in_time_resolver(self, as_of: str, pinned: Optional[Snapshot] = None) -> Callable[[str], Snapshot]:
        def resolve(symbol: str) -> Snapshot:
            if pinned is not None and symbol.lower() in (pinned.meta["symbol"].lower(), pinned.meta["provider_symbol"].lower()):
                return Snapshot(pinned.meta, pinned.df[pinned.df.index <= pd.Timestamp(as_of)])
            meta = self.store.latest_for_symbol(symbol)
            if meta is None:
                raise MidasError("no_data", f"No snapshot for '{symbol}'.", f"Fetch it first: market_fetch(symbol='{symbol}').")
            return self.store.load(meta["id"], as_of=as_of)
        return resolve

    def evidence_add(self, thesis_id: str, *, side: str, kind: str, ref: str = "", title: str = "", quote: str = "",
                     snapshot_id: str = "", expr: str = "") -> dict[str, Any]:
        row = self._row(thesis_id)
        if side not in ("for", "against"):
            raise MidasError("invalid_request", "side must be 'for' or 'against'.", "Evidence is filed on the side it supports.")
        if kind not in EVIDENCE_KINDS:
            raise MidasError("invalid_request", f"Unknown evidence kind '{kind}'.", f"Use one of: {', '.join(EVIDENCE_KINDS)}.")
        title, quote, ref = title.strip()[:200], quote.strip()[:500], ref.strip()
        metric: dict[str, Any] = {}
        if kind == "snapshot_metric":
            sid = snapshot_id or ref
            if not sid:
                raise MidasError("invalid_request", "snapshot_metric evidence needs snapshot_id.", "Pass the snapshot id and optionally expr, e.g. 'yoy(CPIAUCSL)'.")
            snap = self.store.load(sid, as_of=row["as_of"])  # verifies the hash and cuts at the thesis cutoff
            if snap.df.empty:
                raise MidasError("no_data", f"Snapshot {sid} has no rows on or before the thesis cutoff {row['as_of']}.",
                                 "Fetch an earlier range or move the thesis as_of later.")
            expr = expr.strip() or f"close({rules_mod.quote_symbol(snap.meta['symbol'])})"
            evaluator = rules_mod.Evaluator(self.point_in_time_resolver(row["as_of"], pinned=snap))
            got = evaluator.number(expr)
            metric = {"expr": expr, "value": got["value"], "snapshot_id": sid, "as_of": row["as_of"], "data_dates": got["data_dates"],
                      "provenance": provenance(snap.meta)}
            ref = sid
            title = title or f"{expr} = {got['value']:.6g}"
        elif kind == "url":
            if not re.match(r"^https?://", ref):
                raise MidasError("invalid_request", "A url evidence needs ref starting with http:// or https://.", "Give the full address.")
            title = title or ref
        elif kind == "family_ref":
            try:
                parsed = parse_ref(ref)
            except ValueError as error:
                raise MidasError("invalid_request", f"'{ref}' is not a hoard:// reference.", "Format: hoard://<app>/<kind>/<id>.") from error
            if not title:
                raise MidasError("invalid_request", "A family reference needs a title (only the ref and title are stored).", f"Add title='...' for {parsed.uri}.")
            ref, quote = parsed.uri, ""
        else:  # note
            if len(quote) < 3 and len(title) < 3:
                raise MidasError("invalid_request", "A note needs text (quote) or a title.", "Write what the note says.")
            title = title or quote[:80]
        evidence_id = "ev_" + uuid.uuid4().hex[:8]
        self.db.execute("INSERT INTO thesis_evidence(id, thesis_id, side, kind, ref, title, quote, metric, added_ts) VALUES (?,?,?,?,?,?,?,?,?)",
                        (evidence_id, thesis_id, side, kind, ref, title, quote, json.dumps(metric), self.clock()))
        self.db.execute("UPDATE theses SET updated_ts = ? WHERE id = ?", (self.clock(), thesis_id))
        item = next(e for e in self.evidence(thesis_id) if e["id"] == evidence_id)
        return {"thesis": thesis_id, "added": item, "evidence_counts": self._dict(self._row(thesis_id), full=False)["evidence_counts"]}

    def evidence_delete(self, thesis_id: str, evidence_id: str) -> dict[str, Any]:
        self._row(thesis_id)
        row = self.db.one("SELECT id FROM thesis_evidence WHERE id = ? AND thesis_id = ?", (evidence_id, thesis_id))
        if row is None:
            raise MidasError("not_found", f"Thesis {thesis_id} has no evidence '{evidence_id}'.", "Get the thesis to see its evidence ids.")
        self.db.execute("DELETE FROM thesis_evidence WHERE id = ?", (evidence_id,))
        return {"thesis": thesis_id, "deleted": evidence_id}

    # ----------------------------------------------------------------- check
    def _target_for(self, symbol: str, assets: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        for a in assets:
            if symbol.lower() in (str(a.get("symbol", "")).lower(), str(a.get("alias", "")).lower()):
                if a.get("provider"):
                    return {"provider": a["provider"], "symbol": a.get("symbol", symbol), "snapshot_id": a.get("snapshot_id")}
                cat = self._catalogue(a.get("symbol", symbol))
                if cat:
                    return {"provider": cat["provider"], "symbol": cat["symbol"], "snapshot_id": a.get("snapshot_id")}
        cat = self._catalogue(symbol)
        return {"provider": cat["provider"], "symbol": cat["symbol"], "snapshot_id": None} if cat else None

    def check(self, thesis_id: str, *, as_of: Optional[str] = None, refresh: bool = True) -> dict[str, Any]:
        row = self._row(thesis_id)
        thesis = self._dict(row, full=False)
        check_date = iso_date(as_of or self.today(), "as_of")
        if check_date < thesis["as_of"]:
            raise MidasError("invalid_request", f"Check date {check_date} is before the thesis cutoff {thesis['as_of']}.",
                             "Checks run at or after the thesis as_of.")
        if check_date > self.today():
            raise MidasError("invalid_request", f"Check date {check_date} is in the future.", "Use today or earlier.")
        rules = thesis["rules"]
        if not rules:
            return {"thesis": thesis_id, "checked": 0, "tripped": False, "status": thesis["status"],
                    "message": "This thesis has no invalidation rules: add some with thesis_update(rules=[...])."}
        warnings: list[str] = []
        chosen: dict[str, str] = {}  # symbol -> snapshot id used
        symbols: list[str] = []
        for r in rules:
            for s in r["symbols"]:
                if s not in symbols:
                    symbols.append(s)
        for symbol in symbols:
            target = self._target_for(symbol, thesis["assets"])
            meta = None
            if target and target.get("snapshot_id") and not refresh:
                meta = self.store.meta(target["snapshot_id"])
            if refresh and target:
                try:
                    meta, _ = self._fetch(target["provider"], target["symbol"], None, None, "d", {})
                except MidasError as error:
                    warnings.append(f"{symbol}: refresh failed ({error.code}: {error.message}); using the latest stored snapshot.")
            if meta is None:
                meta = (self.store.latest_for_symbol(symbol, target["provider"] if target else None)
                        or self.store.latest_for_symbol(symbol))
            if meta is not None:
                chosen[symbol] = meta["id"]
        cache: dict[str, Snapshot] = {}

        def resolve(symbol: str) -> Snapshot:
            sid = chosen.get(symbol)
            if sid is None:
                raise MidasError("no_data", f"No data for '{symbol}'.", f"Fetch it: market_fetch(symbol='{symbol}', provider=...). Is the provider in thesis assets?")
            if sid not in cache:
                cache[sid] = self.store.load(sid, as_of=check_date)
            return cache[sid]

        evaluator = rules_mod.Evaluator(resolve)
        results = []
        for r in rules:
            res = evaluator.evaluate(r["expr"])
            res["rule_id"] = r["id"]
            stale = []
            for sym, last in res.get("data_dates", {}).items():
                freq = cache[chosen[sym]].meta["frequency"] if sym in chosen and chosen[sym] in cache else "D"
                gap = (pd.Timestamp(check_date) - pd.Timestamp(last)).days
                if gap > STALE_DAYS.get(freq, 10):
                    stale.append(f"{sym}: last observation {last} is {gap} days before {check_date}")
            if stale:
                res["stale"] = stale
                warnings.extend(f"stale data: {x}" for x in stale)
            results.append(res)
        used = sorted({sid for sid in chosen.values()})
        shas = sorted(self.store.meta(s)["sha256"] for s in used)
        signature = hashlib.sha256(json.dumps([check_date, shas, [r["expr"] for r in rules]]).encode()).hexdigest()
        last = self._last_check(thesis_id)
        tripped_any = any(x["state"] == "tripped" for x in results)
        if last and last["signature"] == signature:
            return {"thesis": thesis_id, "reused": True, "check": last, "tripped": last["tripped"], "status": thesis["status"],
                    "message": "Same data and same rules as the last check: nothing to record."}
        status_before = thesis["status"]
        status_after = status_before
        reason = ""
        if tripped_any and status_before in ("open", "confirmed"):
            status_after = "invalidated"
        elif not tripped_any and status_before == "open":
            end = horizon_end(thesis["as_of"], thesis["horizon"])
            if end and check_date > end:
                status_after, reason = "expired", f"horizon ended {end}"
        check_id = "ck_" + uuid.uuid4().hex[:8]
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO thesis_checks(id, thesis_id, ts, as_of, signature, results, tripped, status_before, status_after, snapshots, warnings) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (check_id, thesis_id, self.clock(), check_date, signature, json.dumps(results, default=str), int(tripped_any),
                          status_before, status_after, json.dumps(used), json.dumps(warnings)))
            updated_rules = []
            for r, res in zip(rules, results):
                r = dict(r)
                r["last"] = {"state": res["state"], "as_of": check_date, "check": check_id, "message": res.get("message", ""),
                             "terms": res.get("terms", [])}
                updated_rules.append(r)
            conn.execute("UPDATE theses SET rules = ?, status = ?, updated_ts = ? WHERE id = ?",
                         (json.dumps(updated_rules), status_after, self.clock(), thesis_id))
        if status_after == "invalidated" and status_before != "invalidated":
            trip = next(x for x in results if x["state"] == "tripped")
            self.emit("midas.thesis.invalidated", {"thesis": thesis_id, "title": thesis["title"][:80], "rule": trip["rule_id"], "expr": trip["expr"][:120]})
        return {"thesis": thesis_id, "check": self._check_dict(self.db.one("SELECT * FROM thesis_checks WHERE id = ?", (check_id,))),
                "tripped": tripped_any, "status_before": status_before, "status": status_after, "status_reason": reason, "reused": False,
                "snapshots": [provenance(self.store.meta(s)) for s in used], "warnings": warnings}

    def checks(self, thesis_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self._row(thesis_id)
        rows = self.db.query("SELECT * FROM thesis_checks WHERE thesis_id = ? ORDER BY ts DESC, rowid DESC LIMIT ?", (thesis_id, limit))
        return [self._check_dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for r in self.db.query("SELECT status, COUNT(*) AS n FROM theses GROUP BY status"):
            out[r["status"]] = r["n"]
        return out
