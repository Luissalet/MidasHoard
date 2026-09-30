from __future__ import annotations

import json

import pytest

from conftest import tool
from midas_hoard.agent_tools import AGENT_INSTRUCTIONS, MAX_RESULT_BYTES, TOOLS, call_tool, cap_result, tool_catalog
from midas_hoard.errors import MidasError


def test_catalog_shape_and_description_rules():
    cat = tool_catalog()
    assert len(cat) == 23 and len({t["name"] for t in cat}) == 23
    for t in cat:
        first = t["description"].splitlines()[0]
        assert len(first) <= 110, (t["name"], len(first))
        assert "Sinónimos:" in t["description"], t["name"]
        assert t["inputSchema"]["type"] == "object"
        ann = t["annotations"]
        assert ("readOnlyHint" in ann) or ("destructiveHint" in ann) or ("idempotentHint" in ann)
    assert 0 < len(AGENT_INSTRUCTIONS) < 1500


def test_read_only_tools_are_marked():
    by_name = {t["name"]: t["annotations"] for t in tool_catalog()}
    for name in ("midas_status", "market_providers", "market_search", "market_series", "market_compare", "snapshots_list", "thesis_get",
                 "thesis_list", "strategy_validate", "strategies_list", "experiments_list"):
        assert by_name[name].get("readOnlyHint") is True, name
    for name in ("thesis_update", "thesis_evidence_add", "strategy_save", "portfolio_set"):
        assert by_name[name].get("destructiveHint") is True, name


def test_descriptions_are_bilingual():
    text = " ".join(t["description"].lower() for t in tool_catalog())
    for word in ("mercado", "tesis", "cartera", "thesis", "portfolio", "backtest"):
        assert word in text


def test_unknown_tool_and_bad_arguments(svc):
    with pytest.raises(KeyError):
        call_tool(svc, "nope", {})
    with pytest.raises(Exception):
        call_tool(svc, "market_series", {})  # missing snapshot_id


def test_every_handler_returns_a_dict(svc, fetch):
    sid = fetch()["snapshot"]["id"]
    for name, args in [("midas_status", {}), ("market_providers", {}), ("market_search", {"query": "fake"}), ("snapshots_list", {}),
                       ("market_series", {"snapshot_id": sid}), ("thesis_list", {}), ("strategies_list", {}), ("experiments_list", {}),
                       ("portfolio_analyze", {})]:
        assert isinstance(call_tool(svc, name, args), dict), name


def test_cap_result_trims_the_largest_list():
    data = {"small": [1, 2, 3], "big": [{"x": "y" * 200, "n": i} for i in range(1000)], "meta": "m"}
    out = cap_result(data, limit=20_000)
    assert len(json.dumps(out)) <= 20_500
    assert out["small"] == [1, 2, 3] and len(out["big"]) < 1000
    assert out.get("truncated") or out.get("truncation")


def test_cap_result_leaves_small_results_alone():
    data = {"a": [1, 2], "b": "x"}
    assert cap_result(data) == data


def test_market_providers_credits_sources(svc):
    res = tool(svc, "market_providers")
    assert {p["id"] for p in res["providers"]} >= {"stooq", "fred", "ecb", "coingecko", "csv", "fake"}
    assert "FRED" in res["credits"] and "CoinGecko" in res["credits"]


def test_status_reports_counts_and_disclaimer(svc):
    st = tool(svc, "midas_status")
    assert st["service"] == "midas-hoard" and "counts" in st and "not advice" in st["disclaimer"].lower()


def test_errors_carry_code_and_hint(svc):
    with pytest.raises(MidasError) as e:
        tool(svc, "thesis_get", id="th_nope")
    d = e.value.to_dict()
    assert d["code"] == "not_found" and d["hint"] and d["error"]


def test_uncapped_context_is_for_the_ui_only(svc, fetch):
    from midas_hoard.agent_tools import uncapped
    sid = fetch()["snapshot"]["id"]
    capped = call_tool(svc, "market_series", {"snapshot_id": sid, "limit": 1000, "cursor": 0})
    assert capped.get("truncated") and len(capped["points"]) < 1000
    with uncapped():
        full = call_tool(svc, "market_series", {"snapshot_id": sid, "limit": 1000, "cursor": 0})
    assert "truncated" not in full and len(full["points"]) == 1000


def test_api_routes_are_not_capped_but_agent_calls_are(client):
    sid = client.post("/api/market/fetch", json={"provider": "fake", "symbol": "fake.up"}).json()["snapshot"]["id"]
    ui = client.post("/api/market/series", json={"snapshot_id": sid, "limit": 1000, "cursor": 0}).json()
    assert len(ui["points"]) == 1000 and "truncated" not in ui
    agent = client.post("/api/agent/call", json={"name": "market_series", "arguments": {"snapshot_id": sid, "limit": 1000, "cursor": 0}}, headers=client.bearer).json()
    assert agent["truncated"]["original_lengths"]["points"] == 1000
