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

# ------------------------------------------------------------ persistent lot ids
def _ids(svc, name="lots"):
    return [h["lot_id"] for h in svc.portfolios.get(name)["holdings"]]


def test_every_holding_gets_a_stable_lot_id_on_set_put_csv_and_upsert(svc, lots):
    ids = _ids(svc)
    assert len(ids) == 3 and len(set(ids)) == 3 and all(i.startswith("lot_") for i in ids)
    assert _ids(svc) == ids  # stable across reads
    tool(svc, "portfolio_set", name="lots", action="patch", upsert=[{"symbol": "BBB", "quantity": 7}, {"symbol": "NEW", "quantity": 1},
                                                                    {"symbol": "AAA", "quantity": 3, "new_lot": True}])
    after = _ids(svc)
    assert after[:3] == ids and len(set(after)) == 5  # updates keep ids; new symbol and new_lot get their own
    csv_res = tool(svc, "portfolio_set", name="csv", csv_text="symbol,quantity\nXXX,1\nXXX,2\n")
    assert len({h["lot_id"] for h in csv_res["holdings"]}) == 2
    added = tool(svc, "portfolio_set", name="csv", replace=False, holdings=[{"symbol": "YYY", "quantity": 4}])
    assert [h["lot_id"] for h in added["holdings"]][:2] == [h["lot_id"] for h in csv_res["holdings"]] and len({h["lot_id"] for h in added["holdings"]}) == 3


def test_legacy_portfolios_without_ids_are_given_ids_once_and_persisted(svc):
    import json
    svc.db.execute("INSERT INTO portfolios(name, currency, holdings, updated_ts) VALUES (?,?,?,?)",
                   ("old", "USD", json.dumps([{"symbol": "AAA", "quantity": 1}, {"symbol": "AAA", "quantity": 2}]), 1.0))
    first = _ids(svc, "old")
    assert len(set(first)) == 2 and _ids(svc, "old") == first
    stored = json.loads(svc.db.one("SELECT holdings FROM portfolios WHERE name='old'")["holdings"])
    assert [h["lot_id"] for h in stored] == first  # persisted, not recomputed
    assert svc.portfolios.get("old")["updated_at"] == 1.0


def test_put_keeps_the_ids_of_rows_that_carry_them_and_names_the_rest(svc, lots):
    old = _ids(svc)
    res = tool(svc, "portfolio_set", name="lots", holdings=[{"symbol": "AAA", "quantity": 99, "lot_id": old[1]}, {"symbol": "ZZZ", "quantity": 1},
                                                            {"symbol": "AAA", "quantity": 5, "lot_id": old[1]}])  # a duplicate id is renamed
    got = [h["lot_id"] for h in res["holdings"]]
    assert got[0] == old[1] and got[1] not in old and len(set(got)) == 3


def test_patch_by_lot_id_targets_exactly_that_lot_and_composes_with_other_changes(svc, lots):
    a, b, _ = _ids(svc)
    # the agent changes the quantity of lot a; the user's form (still thinking it is 10 shares) changes its cost: both land
    tool(svc, "portfolio_set", name="lots", action="patch", upsert=[{"symbol": "AAA", "quantity": 12, "lot_id": a}])
    tool(svc, "portfolio_set", name="lots", action="patch", upsert=[{"symbol": "AAA", "cost_basis": 120, "lot_id": a}])
    assert _lots(svc) == [("AAA", 12, 120), ("AAA", 20, 200), ("BBB", 5, None)]
    tool(svc, "portfolio_set", name="lots", action="patch", remove=[{"symbol": "AAA", "lot_id": b}])
    assert _lots(svc) == [("AAA", 12, 120), ("BBB", 5, None)] and _ids(svc)[0] == a


def test_reviewer_scenario_a_replaced_lot_is_a_conflict_not_a_write_to_another_lot(client):
    assert client.put("/api/portfolios/r", json={"currency": "USD", "holdings": [
        {"symbol": "AAA", "quantity": 12, "cost_basis": 110}, {"symbol": "AAA", "quantity": 20, "cost_basis": 200}, {"symbol": "BBB", "quantity": 7}]}).status_code == 200
    loaded = client.get("/api/portfolios/r").json()["holdings"]
    first = loaded[0]["lot_id"]
    # the agent removes the first AAA lot and adds AAA 30/300 in one atomic patch
    agent = client.patch("/api/portfolios/r", json={"remove": [{"lot_id": first}], "upsert": [{"symbol": "AAA", "quantity": 30, "cost_basis": 300, "new_lot": True}]})
    assert agent.status_code == 200
    before = client.get("/api/portfolios/r").json()["holdings"]
    assert [(h["quantity"], h.get("cost_basis")) for h in before] == [(20, 200), (7, None), (30, 300)]
    # the user's stale form edits the first AAA cost: that lot is gone -> 409 and nothing changes
    stale = client.patch("/api/portfolios/r", json={"upsert": [{"symbol": "AAA", "cost_basis": 120, "lot_id": first}, {"symbol": "BBB", "quantity": 9}]})
    assert stale.status_code == 409
    assert client.get("/api/portfolios/r").json()["holdings"] == before
    removal = client.patch("/api/portfolios/r", json={"remove": [{"symbol": "AAA", "lot_id": first}]})
    assert removal.status_code == 409 and client.get("/api/portfolios/r").json()["holdings"] == before


def test_lot_ids_are_in_the_tool_and_analysis_output(svc, portfolio):
    got = svc.portfolios.get("main")["holdings"]
    assert all(h["lot_id"] for h in got)
    res = tool(svc, "portfolio_set", name="main", action="patch", upsert=[{"symbol": "fake.up", "quantity": 11}])
    assert [h["lot_id"] for h in res["holdings"]] == [h["lot_id"] for h in got]
    analysis = tool(svc, "portfolio_analyze", name="main")
    assert {h["lot_id"] for h in analysis["holdings"]} == {h["lot_id"] for h in got}
