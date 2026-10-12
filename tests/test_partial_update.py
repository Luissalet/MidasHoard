from __future__ import annotations

import copy

import pytest

from conftest import tool
from midas_hoard.errors import MidasError
from midas_hoard.patching import merge_patch
from midas_hoard.strategy import EXAMPLE_SPEC


def sid(meta):
    return meta["snapshot"]["id"]


def test_merge_patch_semantics():
    cur = {"a": 1, "n": {"x": 1, "y": 2}, "list": [1, 2], "gone": 1}
    out = merge_patch(cur, {"n": {"y": 3, "z": 4}, "list": [9], "gone": None, "new": "v"})
    assert out == {"a": 1, "n": {"x": 1, "y": 3, "z": 4}, "list": [9], "new": "v"}
    assert cur["n"] == {"x": 1, "y": 2}  # the input is not mutated
    assert merge_patch(None, {"a": {"b": 1}}) == {"a": {"b": 1}}


@pytest.fixture
def portfolio(svc, fetch):
    up, down = sid(fetch("fake.up")), sid(fetch("fake.down"))
    tool(svc, "portfolio_set", name="main", currency="USD", holdings=[
        {"symbol": "fake.up", "quantity": 10, "snapshot_id": up, "currency": "USD", "cost_basis": 100},
        {"symbol": "fake.down", "quantity": 5, "snapshot_id": down, "currency": "USD"},
    ])
    return {"up": up, "down": down}


def test_portfolio_patch_changes_only_given_fields(svc, portfolio):
    # an agent edit lands first ...
    tool(svc, "portfolio_set", name="main", action="patch", upsert=[{"symbol": "fake.down", "quantity": 7}])
    # ... then the user's stale form only sends its own change and does not undo it
    res = tool(svc, "portfolio_set", name="main", action="patch", upsert=[{"symbol": "fake.up", "cost_basis": 120}])
    by = {h["symbol"]: h for h in res["holdings"]}
    assert res["patched"] is True and res["count"] == 2
    assert by["fake.up"]["cost_basis"] == 120 and by["fake.up"]["quantity"] == 10 and by["fake.up"]["snapshot_id"] == portfolio["up"]
    assert by["fake.down"]["quantity"] == 7


def test_portfolio_patch_null_clears_remove_adds_and_currency(svc, portfolio):
    extra = portfolio["up"]
    res = tool(svc, "portfolio_set", name="main", action="patch", currency="eur",
               upsert=[{"symbol": "fake.up", "cost_basis": None}, {"symbol": "NEW", "quantity": 1.5, "snapshot_id": extra}],
               remove=["fake.down", "not-there"])
    by = {h["symbol"]: h for h in res["holdings"]}
    assert res["currency"] == "EUR" and set(by) == {"fake.up", "NEW"}
    assert "cost_basis" not in by["fake.up"] and by["NEW"]["quantity"] == 1.5
    assert tool(svc, "portfolio_analyze")["portfolios"][0]["holdings"] == 2


def test_portfolio_patch_errors_leave_the_portfolio_untouched(svc, portfolio):
    for bad in ({"upsert": [{"symbol": "fake.up", "quantity": "abc"}]}, {"remove": ["fake.up", "fake.down"]}, {"currency": "EU"},
                {"upsert": [{"quantity": 1}]}):
        with pytest.raises(MidasError) as e:
            tool(svc, "portfolio_set", name="main", action="patch", **bad)
        assert e.value.code == "invalid_request"
    with pytest.raises(MidasError) as e:
        tool(svc, "portfolio_set", name="ghost", action="patch", upsert=[{"symbol": "x", "quantity": 1}])
    assert e.value.code == "not_found"
    kept = svc.portfolios.get("main")
    assert kept["currency"] == "USD" and [h["quantity"] for h in kept["holdings"]] == [10, 5]


def test_portfolio_patch_rest(client):
    sid_ = client.post("/api/agent/call", json={"name": "market_fetch", "arguments": {"provider": "fake", "symbol": "fake.up"}}, headers=client.bearer).json()["snapshot"]["id"]
    assert client.put("/api/portfolios/p", json={"currency": "USD", "holdings": [{"symbol": "a", "quantity": 1, "snapshot_id": sid_}]}).status_code == 200
    r = client.patch("/api/portfolios/p", json={"upsert": [{"symbol": "a", "quantity": 4}]})
    assert r.status_code == 200 and r.json()["holdings"][0]["quantity"] == 4 and r.json()["holdings"][0]["snapshot_id"] == sid_
    assert client.patch("/api/portfolios/none", json={"remove": ["a"]}).status_code == 404


@pytest.fixture
def saved(svc, fetch):
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = [sid(fetch("fake.up"))]
    return tool(svc, "strategy_save", spec=spec, note="first")


def test_strategy_spec_patch_merges_nested_and_keeps_the_rest(svc, saved):
    # an agent change to costs lands first, the user's stale editor then only sends the window
    tool(svc, "strategy_save", id=saved["id"], spec_patch={"costs_bps": 9})
    res = tool(svc, "strategy_save", id=saved["id"], spec_patch={"indicators": {"fast": {"window": 10}}})
    assert res["id"] == saved["id"] and res["updated"] is True
    spec = tool(svc, "strategies_list", id=saved["id"])
    assert spec["spec"]["indicators"]["fast"]["window"] == 10 and spec["spec"]["indicators"]["fast"]["type"] == "sma"
    assert spec["spec"]["indicators"]["slow"]["window"] == 50 and spec["spec"]["costs_bps"] == 9
    assert spec["note"] == "first"


def test_strategy_spec_patch_validates_and_refuses_renames(svc, saved):
    with pytest.raises(MidasError) as e:
        tool(svc, "strategy_save", id=saved["id"], spec_patch={"universe": ["snp_nope"]})
    assert e.value.code == "invalid_spec"
    with pytest.raises(MidasError) as e:
        tool(svc, "strategy_save", id=saved["id"], spec_patch={"name": "other"})
    assert e.value.code == "invalid_request"
    with pytest.raises(MidasError) as e:
        tool(svc, "strategy_save", spec_patch={"costs_bps": 1})
    assert e.value.code == "invalid_request"
    assert tool(svc, "strategies_list")["count"] == 1
    assert tool(svc, "strategies_list", id=saved["id"])["spec"]["costs_bps"] == 5


def test_strategy_patch_rest(client):
    r = client.patch("/api/lab/strategies/nope", json={"spec_patch": {"costs_bps": 1}})
    assert r.status_code == 404


# ------------------------------------------------------------ several lots of one symbol
@pytest.fixture
def lots(svc):
    tool(svc, "portfolio_set", name="lots", currency="USD", holdings=[
        {"symbol": "AAA", "quantity": 10, "cost_basis": 100},
        {"symbol": "AAA", "quantity": 20, "cost_basis": 200},
        {"symbol": "BBB", "quantity": 5},
    ])


def _lots(svc):
    return [(h["symbol"], h["quantity"], h.get("cost_basis")) for h in svc.portfolios.get("lots")["holdings"]]


def test_lot_patch_keeps_an_agents_edit_to_another_symbol(svc, lots):
    tool(svc, "portfolio_set", name="lots", action="patch", upsert=[{"symbol": "BBB", "quantity": 7}])  # the agent
    tool(svc, "portfolio_set", name="lots", action="patch",  # the user's form: only the first AAA lot
         upsert=[{"symbol": "AAA", "quantity": 11, "match": {"quantity": 10, "cost_basis": 100}}])
    assert _lots(svc) == [("AAA", 11, 100), ("AAA", 20, 200), ("BBB", 7, None)]


def test_lot_patch_stale_match_is_a_conflict_and_changes_nothing(svc, lots):
    for bad in ({"upsert": [{"symbol": "BBB", "quantity": 9}, {"symbol": "AAA", "quantity": 1, "match": {"quantity": 99}}]},
                {"remove": [{"symbol": "AAA", "match": {"cost_basis": 123}}]}):
        with pytest.raises(MidasError) as e:
            tool(svc, "portfolio_set", name="lots", action="patch", **bad)
        assert e.value.code == "conflict" and e.value.status == 409
    assert _lots(svc) == [("AAA", 10, 100), ("AAA", 20, 200), ("BBB", 5, None)]


def test_lot_patch_new_lot_remove_by_match_and_plain_symbol_still_refused(svc, lots):
    tool(svc, "portfolio_set", name="lots", action="patch", upsert=[{"symbol": "AAA", "quantity": 3, "new_lot": True}])
    assert _lots(svc)[-1] == ("AAA", 3, None) and len(_lots(svc)) == 4
    tool(svc, "portfolio_set", name="lots", action="patch", remove=[{"symbol": "AAA", "match": {"quantity": 20, "cost_basis": 200}}])
    assert _lots(svc) == [("AAA", 10, 100), ("BBB", 5, None), ("AAA", 3, None)]
    with pytest.raises(MidasError) as e:
        tool(svc, "portfolio_set", name="lots", action="patch", upsert=[{"symbol": "AAA", "quantity": 1}])
    assert e.value.code == "invalid_request"
    tool(svc, "portfolio_set", name="lots", action="patch", remove=["AAA"])
    assert _lots(svc) == [("BBB", 5, None)]


def test_lot_patch_rest_returns_409_for_a_stale_lot(client):
    assert client.put("/api/portfolios/m", json={"currency": "USD", "holdings": [{"symbol": "A", "quantity": 1}, {"symbol": "A", "quantity": 2}]}).status_code == 200
    ok = client.patch("/api/portfolios/m", json={"upsert": [{"symbol": "A", "quantity": 5, "match": {"quantity": 2}}]})
    assert ok.status_code == 200 and [h["quantity"] for h in ok.json()["holdings"]] == [1, 5]
    stale = client.patch("/api/portfolios/m", json={"upsert": [{"symbol": "A", "quantity": 6, "match": {"quantity": 2}}]})
    assert stale.status_code == 409
    assert [h["quantity"] for h in client.get("/api/portfolios/m").json()["holdings"]] == [1, 5]
