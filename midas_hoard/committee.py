"""The committee: two advocates, an independent risk reviewer and an arbiter, over a fixed evidence pack.

Model-backed through Hoard Link (capability ``llm``). Advocates may cite only evidence ids of the pack; the risk reviewer
works without seeing them; the arbiter *judges* — it is told explicitly that its verdict is not an average of the votes.
Every text goes through the number ledger. Without a model, or when the model fails, ``run`` returns the evidence pack
as ``material`` plus deterministic metrics: a caller (or a person) can do the weighing.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import pandas as pd

from .db import Database
from .errors import MidasError
from .ledger import Ledger
from .series import simple_stats
from .snapshots import SnapshotStore, provenance
from .theses import Theses

VERDICTS = ("strengthen", "weaken", "inconclusive")
JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
GUIDE = ("Numbers: use only numbers that appear in the evidence pack. If you must introduce another number (a target, an assumption) "
         "write it followed by [proposed]. To cite a number from an evidence item write it followed by its label, e.g. 4.1 [E3]. "
         "Cite evidence only by the ids in allowed_evidence_ids. Answer with JSON only.")


def build_pack(theses: Theses, store: SnapshotStore, thesis_id: str) -> dict[str, Any]:
    """Everything the committee (or a person) may rely on, cut at the thesis cutoff; plus the values the ledger recognises."""
    t = theses.get(thesis_id)
    as_of = t["as_of"]
    values: list[dict[str, Any]] = []
    cited: dict[str, str] = {"thesis": " ".join([t["title"], t["claim"], t["rival"], t["horizon"], *t["assumptions"],
                                                 *[r["expr"] + " " + r["description"] for r in t["rules"]]])}
    evidence = []
    for e in t["evidence"]:
        item = {"label": e["label"], "side": e["side"], "kind": e["kind"], "title": e["title"], "quote": e["quote"], "ref": e["ref"]}
        if e["kind"] == "snapshot_metric":
            m = e["metric"]
            item["metric"] = {"expr": m.get("expr"), "value": m.get("value"), "as_of": m.get("as_of"), "snapshot": m.get("snapshot_id"),
                              "data_dates": m.get("data_dates")}
            values.append({"value": m.get("value"), "label": f"{e['label']} {m.get('expr')}", "kind": "observed", "source": e["ref"]})
        cited[e["label"]] = f"{e['title']} {e['quote']}"
        evidence.append(item)
    assets, missing = [], []
    seen: set[str] = set()
    for a in t["assets"]:
        sid = a.get("snapshot_id")
        if not sid and str(a.get("symbol", "")).startswith("snp_"):
            sid = a["symbol"]  # assets stored before snapshot ids were recognised
        try:
            meta = store.meta(sid) if sid else store.latest_for_symbol(a["symbol"], a.get("provider")) or store.latest_for_symbol(a["symbol"])
        except MidasError:
            meta = None
        if meta is None or meta["id"] in seen:
            if meta is None:
                missing.append(a.get("symbol", "?"))
            continue
        seen.add(meta["id"])
        snap = store.load(meta["id"], as_of=as_of)
        if snap.df.empty:
            missing.append(f"{meta['symbol']} (no data on or before {as_of})")
            continue
        close = snap.df["close"].dropna()
        ppy = float(meta.get("periods_per_year") or 252.0)
        year = close[close.index >= close.index[-1] - pd.DateOffset(years=1)]
        st = simple_stats(year, ppy)
        full = simple_stats(close, ppy)
        metrics = {"last": st.get("last"), "last_date": st.get("last_date"), "trailing_1y_return": st.get("total_return"),
                   "trailing_1y_vol": st.get("ann_vol"), "trailing_1y_max_drawdown": st.get("max_drawdown"),
                   "full_history_max_drawdown": full.get("max_drawdown"), "current_drawdown": full.get("current_drawdown"),
                   "observations": int(len(close))}
        for k, v in metrics.items():
            if isinstance(v, (int, float)) and k != "observations":
                values.append({"value": v, "label": f"{meta['symbol']} {k}", "kind": "derived" if k != "last" else "observed", "source": meta["id"]})
        assets.append({"symbol": meta["symbol"], "snapshot": provenance(meta), "metrics": metrics})
    rules = []
    for r in t["rules"]:
        last = r.get("last") or {}
        rules.append({"id": r["id"], "expr": r["expr"], "state": last.get("state", "unchecked"), "as_of": last.get("as_of")})
        for term in last.get("terms", []):
            for k in ("lhs", "rhs"):
                if isinstance(term.get(k), (int, float)):
                    values.append({"value": term[k], "label": f"{r['id']} {term['expr']} ({k})", "kind": "observed", "source": "thesis_check"})
    dates = {as_of, t["horizon_end"] or as_of, *[a["metrics"]["last_date"] for a in assets if a["metrics"].get("last_date")]}
    for e in evidence:
        for d in (e.get("metric") or {}).get("data_dates", {}).values():
            dates.add(d)
    pack = {
        "thesis": {"id": t["id"], "title": t["title"], "claim": t["claim"], "as_of": as_of, "horizon": t["horizon"], "status": t["status"],
                   "rival_hypothesis": t["rival"], "assumptions": t["assumptions"]},
        "evidence": evidence, "allowed_evidence_ids": [e["label"] for e in evidence], "assets": assets, "rules": rules,
        "missing_data": missing,
        "note": f"All data is cut at {as_of}; nothing later is used.",
    }
    digest = hashlib.sha256(json.dumps(pack, sort_keys=True, default=str).encode()).hexdigest()[:16]
    pack["digest"] = digest
    return {"pack": pack, "ledger_values": values, "cited_texts": cited, "dates": sorted(dates)}


def deterministic_summary(pack: dict[str, Any]) -> dict[str, Any]:
    ev = pack["evidence"]
    states = [r["state"] for r in pack["rules"]]
    return {
        "evidence_for": sum(1 for e in ev if e["side"] == "for"), "evidence_against": sum(1 for e in ev if e["side"] == "against"),
        "measured_evidence": sum(1 for e in ev if e["kind"] == "snapshot_metric"),
        "rules": {"tripped": states.count("tripped"), "clear": states.count("clear"), "no_data": states.count("no_data"),
                  "error": states.count("error"), "unchecked": states.count("unchecked")},
        "assets_with_data": len(pack["assets"]), "assets_missing_data": pack["missing_data"],
        "balance_note": "Counts are not weights: one measured, dated metric outweighs several opinions. Weigh them yourself.",
    }


def _parse_json(text: str) -> Optional[Any]:
    body = JSON_FENCE.sub("", (text or "").strip())
    try:
        return json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(body[start:end + 1])
            except ValueError:
                return None
    return None


class Committee:
    def __init__(self, db: Database, theses: Theses, store: SnapshotStore, link: Any, clock: Callable[[], float] = time.time,
                 model_gate: Optional[Callable[[], str]] = None):
        self.db, self.theses, self.store, self.link, self.clock = db, theses, store, link, clock
        self.model_gate = model_gate  # returns why the model is known to be down (from the cached probe), or ''

    def _ask(self, system: str, payload: dict[str, Any], effort: str) -> tuple[Any, str, str]:
        if self.model_gate is not None:
            why = self.model_gate()
            if why:
                raise RuntimeError(f"model unavailable (cached resolution): {why}")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]
        result = self.link.chat(messages, response_format={"type": "json_object"}, effort=effort, max_tokens=2500, temperature=0.2)
        return _parse_json(result.text), result.text, getattr(result, "model", "") or ""

    @staticmethod
    def _clean_items(raw: Any, key: str, allowed: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
        """Keep at most 6 items; drop citations that are not evidence ids of the pack and report them."""
        flags: list[str] = []
        items = raw.get(key) if isinstance(raw, dict) else raw
        out = []
        for item in (items or [])[:6]:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue
            cites = [str(c).strip().upper() for c in (item.get("evidence") or []) if str(c).strip()]
            bad = [c for c in cites if c not in allowed]
            if bad:
                flags.append(f"dropped citation(s) {', '.join(bad)}: not evidence ids of this thesis")
            good = [c for c in cites if c in allowed]
            out.append({"text": str(item["text"]).strip()[:800], "evidence": good, "uncited": not good})
        return out, flags

    def run(self, thesis_id: str, *, use_model: bool = True) -> dict[str, Any]:
        built = build_pack(self.theses, self.store, thesis_id)
        pack = built["pack"]
        ledger = Ledger(built["ledger_values"], built["cited_texts"], built["dates"])
        base = {"thesis": thesis_id, "pack_digest": pack["digest"], "as_of": pack["thesis"]["as_of"], "deterministic": deterministic_summary(pack)}
        result = self._material(base, pack, "model use was switched off (use_model=false)") if not use_model else self._with_model(base, pack, ledger)
        run_id = "cm_" + uuid.uuid4().hex[:8]
        result["id"] = run_id
        self.db.execute("INSERT INTO committee_runs(id, thesis_id, created_ts, mode, result) VALUES (?,?,?,?,?)",
                        (run_id, thesis_id, self.clock(), result["mode"], json.dumps(result, default=str)))
        return result

    @staticmethod
    def _material(base: dict[str, Any], pack: dict[str, Any], reason: str) -> dict[str, Any]:
        return {**base, "mode": "material", "reason": reason, "verdict": None, "material": pack,
                "instructions": ("No model produced this. Weigh the evidence pack yourself: argue both sides using only the allowed_evidence_ids, "
                                 "list what could invalidate the thesis, and state what evidence is missing. Quote numbers only from the pack.")}

    def _with_model(self, base: dict[str, Any], pack: dict[str, Any], ledger: Ledger) -> dict[str, Any]:
        allowed = set(pack["allowed_evidence_ids"])
        common = {"evidence_pack": pack, "allowed_evidence_ids": sorted(allowed)}
        side_sys = ("You are an advocate on an investment-research committee. Argue {side} the thesis using only the evidence pack. "
                    "Write at most 5 arguments, each one or two sentences. " + GUIDE +
                    ' Format: {{"arguments": [{{"text": "...", "evidence": ["E1"]}}]}}')
        risk_sys = ("You are the independent risk reviewer. You have not heard the advocates. List what could invalidate or badly hurt the "
                    "thesis: missing data, hidden assumptions, regime changes, concentration. At most 5 items. " + GUIDE +
                    ' Format: {"risks": [{"text": "...", "evidence": ["E2"]}]}')
        arbiter_sys = ("You are the arbiter. Decide whether the committee's material strengthens, weakens or leaves the thesis inconclusive. "
                       "Your verdict is a judgement about the quality and independence of the evidence, NOT an average of the votes: "
                       "one measured, dated metric can outweigh several opinions, and an unanswered risk can outweigh several arguments. "
                       + GUIDE + ' Format: {"verdict": "strengthen|weaken|inconclusive", "confidence": 0-100, "reasons": '
                       '[{"text": "...", "evidence": ["E1"]}], "missing_evidence": ["..."]}')
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f_for = pool.submit(self._ask, side_sys.format(side="FOR"), common, "high")
                f_against = pool.submit(self._ask, side_sys.format(side="AGAINST"), common, "high")
                got_for, got_against = f_for.result(), f_against.result()
            got_risk = self._ask(risk_sys, common, "high")
            adv_for, flags_for = self._clean_items(got_for[0], "arguments", allowed)
            adv_against, flags_against = self._clean_items(got_against[0], "arguments", allowed)
            risks, flags_risk = self._clean_items(got_risk[0], "risks", allowed)
            arb_payload = {**common, "advocate_for": adv_for, "advocate_against": adv_against, "risk_review": risks}
            got_arb = self._ask(arbiter_sys, arb_payload, "max")
        except Exception as error:  # noqa: BLE001 — no model, model down, or an unusable answer: degrade, never fail
            return self._material(base, pack, f"model unavailable ({type(error).__name__}: {str(error)[:160]})")
        if not any([adv_for, adv_against, risks]) or not isinstance(got_arb[0], dict):
            return self._material(base, pack, "the model's answers could not be parsed as the requested JSON")
        arb = got_arb[0]
        verdict = str(arb.get("verdict", "")).lower().strip()
        arb_flags: list[str] = []
        if verdict not in VERDICTS:
            arb_flags.append(f"verdict {verdict!r} is not one of {list(VERDICTS)}: recorded as inconclusive")
            verdict = "inconclusive"
        try:
            confidence = int(max(0, min(100, round(float(arb.get("confidence", 0))))))
        except (TypeError, ValueError):
            confidence = 0
            arb_flags.append("confidence was not a number: recorded as 0")
        reasons, rflags = self._clean_items(arb, "reasons", allowed)
        missing = [str(m).strip()[:300] for m in (arb.get("missing_evidence") or []) if str(m).strip()][:8]

        def audit(items: list[dict[str, Any]]) -> None:
            for it in items:
                res = ledger.check(it["text"])
                it["ledger"] = {"ok": res["ok"], "unmatched": res["unmatched"], "counts": res["counts"]}

        for group in (adv_for, adv_against, risks, reasons):
            audit(group)
        missing_ledger = [ledger.check(m) for m in missing]
        unmatched = []
        for label, group in (("advocate_for", adv_for), ("advocate_against", adv_against), ("risk_review", risks), ("arbiter.reasons", reasons)):
            for i, it in enumerate(group):
                for u in it["ledger"]["unmatched"]:
                    unmatched.append({"section": label, "item": i + 1, **u})
        for i, res in enumerate(missing_ledger):
            for u in res["unmatched"]:
                unmatched.append({"section": "arbiter.missing_evidence", "item": i + 1, **u})
        return {
            **base, "mode": "model", "verdict": verdict, "confidence": confidence, "model": got_arb[2] or got_for[2],
            "method": "arbiter judgement over two advocates and an independent risk review — not an average of the votes",
            "advocate_for": adv_for, "advocate_against": adv_against, "risk_review": risks,
            "arbiter": {"verdict": verdict, "confidence": confidence, "reasons": reasons, "missing_evidence": missing},
            "flags": flags_for + flags_against + flags_risk + rflags + arb_flags,
            "ledger": {"ok": not unmatched, "unmatched": unmatched, "numbers_checked": sum(
                it["ledger"]["counts"] and sum(it["ledger"]["counts"].values()) or 0 for g in (adv_for, adv_against, risks, reasons) for it in g),
                "note": "Unmatched numbers are not in the evidence pack, not cited to an evidence item and not marked [proposed]: treat them as unverified."},
            "evidence_labels": pack["allowed_evidence_ids"],
        }

    def latest(self, thesis_id: str) -> Optional[dict[str, Any]]:
        row = self.db.one("SELECT * FROM committee_runs WHERE thesis_id = ? ORDER BY created_ts DESC, rowid DESC LIMIT 1", (thesis_id,))
        return json.loads(row["result"]) if row else None

    def history(self, thesis_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id, created_ts, mode, result FROM committee_runs WHERE thesis_id = ? ORDER BY created_ts DESC, rowid DESC LIMIT ?",
                             (thesis_id, limit))
        out = []
        for r in rows:
            res = json.loads(r["result"])
            out.append({"id": r["id"], "created_at": r["created_ts"], "mode": r["mode"], "verdict": res.get("verdict"),
                        "confidence": res.get("confidence"), "unmatched_numbers": len((res.get("ledger") or {}).get("unmatched", []))})
        return out
