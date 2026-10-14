"""Duplicates by mechanism: the same idea under another name still counts as a trial."""

from __future__ import annotations

import copy

import pytest

from conftest import tool
from midas_hoard import duplicates as dup
from midas_hoard.errors import MidasError
from midas_hoard.strategy import EXAMPLE_SPEC


def trend(universe, name, fast=20, slow=50, fast_name="fast", slow_name="slow", note="", family=None):
    spec = {
        "name": name, "universe": universe, "note": note,
        "indicators": {fast_name: {"type": "sma", "window": fast}, slow_name: {"type": "sma", "window": slow}},
        "entry": {"all": [{"left": fast_name, "op": ">", "right": slow_name}]}, "exit": {"all": [{"left": fast_name, "op": "<", "right": slow_name}]},
        "sizing": {"type": "all_in"}, "rebalance": "daily", "costs_bps": 5, "slippage_bps": 2,
    }
    if family:
        spec["family"] = family
    return spec


def reversion(universe, name, note=""):
    return {
        "name": name, "universe": universe, "note": note, "indicators": {"r": {"type": "rsi", "window": 14}},
        "entry": {"all": [{"left": "r", "op": "<", "right": 30}]}, "exit": {"all": [{"left": "r", "op": ">", "right": 55}]},
        "sizing": {"type": "all_in"}, "rebalance": "daily", "costs_bps": 5, "slippage_bps": 2,
    }


@pytest.fixture
def sid(fetch):
    return fetch("fake.up")["snapshot"]["id"]


def test_features_ignore_names_and_compare_indicators_by_kind():
    a = dup.spec_features(trend(["u"], "alpha"))
    b = dup.spec_features(trend(["u"], "omega", fast=7, slow=90, fast_name="ma_short", slow_name="ma_long"))
    assert a["indicators"] == b["indicators"] and a["rules"] == b["rules"]
    m = dup.mechanism_score(a, b)
    assert m["score"] == 1.0 and any("only parameters differ" in w for w in m["why"]) and any("identical" in w for w in m["why"])
    c = dup.spec_features(reversion(["u"], "rev"))
    assert dup.mechanism_score(a, c)["score"] < dup.MEDIUM_MECHANISM


def test_same_logic_on_another_universe_is_still_the_same_hypothesis():
    m = dup.mechanism_score(dup.spec_features(trend(["x"], "a")), dup.spec_features(trend(["y"], "b")))
    assert m["score"] == pytest.approx(0.8) and m["score"] >= dup.HIGH_MECHANISM and any("different universe" in w for w in m["why"])


def test_text_similarity_lexical_and_embedding_and_fallback():
    scores, method = dup.text_scores("buy oversold dips in technology stocks", ["buy oversold dips in technology shares", "monthly rebalanced global bond ladder"])
    assert method == "tfidf" and scores[0] > 0.6 > scores[1]
    assert dup.text_scores("x", ["y"]) == ([None], "none")  # too little text to compare

    scores, method = dup.text_scores("alpha beta gamma", ["alpha beta gamma", "delta epsilon zeta"], embedder=lambda ts: [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    assert method == "embedding" and scores == [pytest.approx(1.0), pytest.approx(0.0)]

    def broken(texts):
        raise RuntimeError("hub down")
    scores, method = dup.text_scores("alpha beta gamma", ["alpha beta gamma"], embedder=broken)
    assert method == "tfidf" and scores[0] == pytest.approx(1.0)


def test_level_rules():
    assert dup.level_for(0.8, None, "tfidf") == "high"
    assert dup.level_for(0.5, 0.9, "tfidf") == "high"
    assert dup.level_for(0.5, 0.2, "tfidf") == "medium"
    assert dup.level_for(0.2, 0.95, "tfidf") == "medium"
    assert dup.level_for(0.2, 0.85, "embedding") == "low" and dup.level_for(0.2, 0.9, "embedding") == "medium"
    assert dup.level_for(0.1, None, "none") == "low"


def test_renamed_variant_in_another_family_is_flagged_high(svc, sid):
    first = tool(svc, "backtest_run", spec=trend([sid], "trend-alpha"))
    assert first["duplicate"]["level"] == "low" and first["variants"]["effective"] == 1
    second = tool(svc, "backtest_run", spec=trend([sid], "momentum-omega", fast=10, slow=30, fast_name="ma_short", slow_name="ma_long"))
    d = second["duplicate"]
    assert d["level"] == "high" and d["similar"][0]["experiment"] == first["run_id"] and d["similar"][0]["family"] == "trend-alpha"
    assert any("identical" in w for w in d["similar"][0]["why"])
    assert second["spec_hash"] != first["spec_hash"]
    # it counts: the second family has one variant of its own plus the one under the other name
    assert second["variants"]["tried"] == 1 and second["variants"]["related"] == 1 and second["variants"]["effective"] == 2
    assert second["multiple_testing"]["variants_tried"] == 2
    # and the report says so
    rep = svc.lab.run_report(second["run_id"])
    gates = {g["id"]: g for s in rep["sections"] if s["id"] == "gates" for g in s["gates"]}
    assert gates["duplicate"]["status"] == "warn" and "high" in gates["duplicate"]["text"]
    risks = " ".join(c["text"] for s in rep["sections"] if s["id"] == "risks" for c in s["claims"])
    assert "trend-alpha" in risks
    md = tool(svc, "report_export", kind="run", id=second["run_id"], format="md", save=False)["markdown"]
    assert "Similar to" in md and "experiments_duplicates" in md


def test_different_logic_is_low_and_does_not_count(svc, sid):
    tool(svc, "backtest_run", spec=trend([sid], "trend-alpha"))
    other = tool(svc, "backtest_run", spec=reversion([sid], "dip-buyer"))
    assert other["duplicate"]["level"] == "low" and other["variants"]["related"] == 0 and other["variants"]["effective"] == 1


def test_same_family_variants_are_not_duplicates_of_each_other(svc, sid):
    tool(svc, "backtest_run", spec=trend([sid], "trend-alpha"))
    again = tool(svc, "backtest_run", spec=trend([sid], "trend-alpha-12", fast=12, family="trend-alpha"))
    assert again["duplicate"]["level"] == "low" and again["variants"]["tried"] == 2 and again["variants"]["related"] == 0


def test_warning_level_follows_effective_variants(svc, sid):
    runs = [tool(svc, "backtest_run", spec=trend([sid], f"idea-{i}", fast=5 + 3 * i, slow=40 + 7 * i, fast_name=f"a{i}", slow_name=f"b{i}")) for i in range(5)]
    last = runs[-1]
    assert last["variants"]["tried"] == 1 and last["variants"]["effective"] == 5 and last["variants"]["warning_level"] == "moderate"
    assert last["multiple_testing"]["warning_level"] == "moderate" and last["multiple_testing"]["variants_tried"] == 5
    assert runs[0]["variants"]["warning_level"] == "none"  # the first one was alone when it was registered
    log = tool(svc, "experiments_list")
    fam0 = next(f for f in log["families"] if f["family"] == "idea-0")
    assert fam0["variants"] == 1 and fam0["related_variants"] == 4 and fam0["effective_variants"] == 5 and fam0["warning_level"] == "moderate"
    stats = svc.lab.family_stats("idea-3")
    assert stats["effective_variants"] == 5 and len(stats["trial_sharpes"]) == 5


def test_text_similarity_raises_to_medium_when_the_logic_differs(svc, sid):
    note = "buy oversold dips in large technology stocks and sell the rebound"
    tool(svc, "backtest_run", spec=trend([sid], "tech-one", note=note))
    run = tool(svc, "backtest_run", spec=reversion([sid], "tech-two", note=note))
    d = run["duplicate"]
    assert d["level"] == "medium" and d["text_method"] == "tfidf" and any("descriptions are similar" in w for w in d["similar"][0]["why"])
    assert run["variants"]["related"] == 0  # description alone never changes the count: that stays reproducible


def test_embedder_is_used_when_given_and_failure_falls_back(svc, sid):
    note = "carry strategy on global bond futures"
    tool(svc, "backtest_run", spec=trend([sid], "bond-one", note=note))
    svc.lab.embedder = lambda texts: [[1.0, 0.0] for _ in texts]
    run = tool(svc, "backtest_run", spec=reversion([sid], "bond-two", note=note + " extended"))
    assert run["duplicate"]["text_method"] == "embedding" and run["duplicate"]["level"] == "medium"

    def down(texts):
        raise RuntimeError("no hub")
    svc.lab.embedder = down
    run2 = tool(svc, "backtest_run", spec=reversion([sid], "bond-three", note=note))
    assert run2["duplicate"]["text_method"] == "tfidf"


def test_experiments_list_shows_the_risk(svc, sid):
    a = tool(svc, "backtest_run", spec=trend([sid], "trend-alpha"))
    b = tool(svc, "backtest_run", spec=trend([sid], "trend-beta", fast=9, slow=33, fast_name="m", slow_name="n"))
    items = {e["id"]: e for e in tool(svc, "experiments_list", kind="backtest")["experiments"]}
    assert items[b["run_id"]]["duplicate_risk"] == "high" and items[b["run_id"]]["duplicate_similar"][0]["experiment"] == a["run_id"]
    assert items[a["run_id"]]["duplicate_risk"] == "low"  # registered alone; a later copy does not rewrite history
    assert items[b["run_id"]]["effective_variants"] == 2
    detail = tool(svc, "experiments_list", run_id=b["run_id"])
    assert detail["duplicate"]["level"] == "high"


def test_duplicates_tool_for_run_spec_strategy_and_listing(svc, sid):
    a = tool(svc, "backtest_run", spec=trend([sid], "trend-alpha"))
    b = tool(svc, "backtest_run", spec=trend([sid], "trend-beta", fast=9, slow=33, fast_name="m", slow_name="n"))
    one = tool(svc, "experiments_duplicates", run_id=b["run_id"])
    assert one["level"] == "high" and one["similar"][0]["experiment"] == a["run_id"] and one["method"]
    hypo = tool(svc, "experiments_duplicates", spec=trend([sid], "trend-gamma", fast=15, slow=45, fast_name="p", slow_name="q"))
    assert hypo["hypothetical"] is True and hypo["level"] == "high" and hypo["similar_total"] == 2
    tool(svc, "strategy_save", spec=reversion([sid], "dip-buyer"))
    assert tool(svc, "experiments_duplicates", strategy="dip-buyer")["level"] == "low"
    listing = tool(svc, "experiments_duplicates", min_level="high")
    assert listing["total"] == 2 and {e["experiment"] for e in listing["experiments"]} == {a["run_id"], b["run_id"]}
    assert tool(svc, "experiments_duplicates", min_level="low")["total"] >= 2
    with pytest.raises(MidasError):
        tool(svc, "experiments_duplicates", run_id="bt_missing")
    with pytest.raises(MidasError) as e:
        tool(svc, "experiments_duplicates", spec={"name": "bad"})
    assert e.value.code == "invalid_spec"


def test_a_high_duplicate_caps_a_report_at_review():
    from midas_hoard import labreport as lr
    from test_labreport import good_facts
    assert lr.decide(good_facts(duplicate_risk="high"))["decision"] == "review"


def test_example_spec_runs_alone_as_low(svc, sid):
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = [sid]
    run = tool(svc, "backtest_run", spec=spec)
    assert run["duplicate"]["level"] == "low" and run["duplicate"]["compared_with"] == 0
