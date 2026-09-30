from __future__ import annotations

import json

from conftest import RoleLink, T0, make_config, tool
from midas_hoard.agent_tools import call_tool
from midas_hoard.services import Services


def build(tmp_path, link):
    return Services(make_config(tmp_path), link=link, clock_fn=lambda: T0, sleep_fn=lambda _s: None)


def seed(svc):
    tool(svc, "market_fetch", provider="fake", symbol="fake.up")
    t = tool(svc, "thesis_create", title="Trend persists", claim="fake.up keeps rising", rival="It is only noise from a seeded generator.",
             as_of="2023-06-30", assets=["fake.up"], rules=["close(fake.up) < 50"])
    sid = svc.store.latest_for_symbol("fake.up")["id"]
    tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="for", kind="snapshot_metric", snapshot_id=sid, title="last close")
    tool(svc, "thesis_evidence_add", thesis_id=t["id"], side="against", kind="note", title="Valuation", quote="The index trades at 31.5 times earnings.")
    return t


GOOD = {
    "for": {"arguments": [{"text": "The measured last close supports the trend.", "evidence": ["E1"]}]},
    "against": {"arguments": [{"text": "Valuation is stretched at 31.5 times earnings [E2].", "evidence": ["E2", "E9"]}]},
    "risk": {"risks": [{"text": "A drawdown past 99% would end the thesis.", "evidence": []}]},
    "arbiter": {"verdict": "inconclusive", "confidence": 55, "reasons": [{"text": "One dated metric, one opinion.", "evidence": ["E1"]}],
                "missing_evidence": ["Earnings revisions"]},
}


def test_without_model_returns_material(svc):
    t = seed(svc)
    res = tool(svc, "committee_run", thesis_id=t["id"], use_model=False)
    assert res["mode"] == "material" and res["verdict"] is None
    assert res["material"]["allowed_evidence_ids"] == ["E1", "E2"]
    assert res["deterministic"]["evidence_for"] == 1


def test_model_failure_degrades_to_material(svc):  # the default fixture link raises
    t = seed(svc)
    res = tool(svc, "committee_run", thesis_id=t["id"])
    assert res["mode"] == "material" and "model unavailable" in res["reason"]


def test_full_committee_flow_with_ledger_and_citation_check(tmp_path):
    link = RoleLink(GOOD)
    svc = build(tmp_path, link)
    try:
        t = seed(svc)
        res = tool(svc, "committee_run", thesis_id=t["id"])
        assert res["mode"] == "model" and res["verdict"] == "inconclusive" and res["confidence"] == 55
        assert len(link.calls) == 4
        roles = sorted(c["system"].split(".")[0][:40] for c in link.calls)
        assert any("arbiter" in r.lower() for r in roles)
        # the risk reviewer must not see the advocates
        risk_call = next(c for c in link.calls if "risk reviewer" in c["system"].lower()[:60])
        assert "advocate_for" not in risk_call["payload"]
        # the arbiter sees them
        arb_call = next(c for c in link.calls if "arbiter" in c["system"].lower()[:40])
        assert "advocate_for" in arb_call["payload"] and "risk_review" in arb_call["payload"]
        # E9 is not an evidence id: dropped and reported
        assert res["advocate_against"][0]["evidence"] == ["E2"]
        assert any("E9" in f for f in res["flags"])
        # 31.5 is cited in E2's text; 99% appears nowhere: flagged
        texts = [u["text"] for u in res["ledger"]["unmatched"]]
        assert any("99" in x for x in texts) and not any("31.5" in x for x in texts)
        assert res["ledger"]["ok"] is False
    finally:
        svc.stop()


def test_bad_verdict_is_recorded_as_inconclusive(tmp_path):
    handlers = {**GOOD, "arbiter": {"verdict": "buy", "confidence": "high", "reasons": []}}
    svc = build(tmp_path, RoleLink(handlers))
    try:
        t = seed(svc)
        res = tool(svc, "committee_run", thesis_id=t["id"])
        assert res["verdict"] == "inconclusive" and res["confidence"] == 0
        assert len(res["flags"]) >= 2
    finally:
        svc.stop()


def test_unparseable_model_output_degrades(tmp_path):
    svc = build(tmp_path, RoleLink({"for": "not json", "against": "nope", "risk": "{}", "arbiter": "x"}))
    try:
        t = seed(svc)
        assert tool(svc, "committee_run", thesis_id=t["id"])["mode"] == "material"
    finally:
        svc.stop()


def test_runs_are_stored_and_listed_in_thesis_get(svc):
    t = seed(svc)
    tool(svc, "committee_run", thesis_id=t["id"], use_model=False)
    got = tool(svc, "thesis_get", id=t["id"])
    assert got["committee"] and got["committee"][0]["mode"] == "material"
