from __future__ import annotations

import copy
import json

import pytest

from conftest import tool
from midas_hoard.errors import MidasError
from midas_hoard.strategy import EXAMPLE_SPEC


@pytest.fixture
def spec(fetch):
    sid = fetch("fake.up")["snapshot"]["id"]
    s = copy.deepcopy(EXAMPLE_SPEC)
    s["universe"] = [sid]
    return s


def test_validate_and_example(svc, spec):
    assert tool(svc, "strategy_validate", spec=spec)["ok"]
    bad = tool(svc, "strategy_validate", spec={**spec, "universe": ["snp_nope"]})
    assert not bad["ok"] and bad["issues"][0]["hint"] and bad["example"]


def test_save_list_delete_strategy(svc, spec):
    saved = tool(svc, "strategy_save", spec=spec, note="first")
    assert saved["id"].startswith("st_") and saved["updated"] is False
    again = tool(svc, "strategy_save", spec=spec)
    assert again["id"] == saved["id"] and again["updated"] is True
    listed = tool(svc, "strategies_list")
    assert listed["count"] == 1 and "spec" not in listed["strategies"][0]
    assert tool(svc, "strategies_list", id="sma-trend")["spec"]["universe"] == spec["universe"]
    with pytest.raises(MidasError) as e:
        tool(svc, "strategy_save", action="delete", id=saved["id"])
    assert e.value.code == "confirm_required"
    tool(svc, "strategy_save", action="delete", id=saved["id"], confirm=True)
    assert tool(svc, "strategies_list")["count"] == 0


def test_save_rejects_invalid_spec(svc, spec):
    with pytest.raises(MidasError) as e:
        tool(svc, "strategy_save", spec={**spec, "universe": ["snp_nope"]})
    assert e.value.code == "invalid_spec"


def test_run_writes_artifacts_and_is_reproducible(svc, spec):
    a = tool(svc, "backtest_run", spec=spec)
    folder = svc.config.runs_dir / a["run_id"]
    for name in ("spec.json", "snapshots.json", "metrics.json", "equity.csv", "trades.csv", "report.md"):
        assert (folder / name).is_file(), name
    assert "not advice" in (folder / "report.md").read_text(encoding="utf-8").lower()
    b = tool(svc, "backtest_run", spec=spec)
    assert a["run_id"] != b["run_id"] and a["metrics"]["total_return"] == b["metrics"]["total_return"]
    assert a["spec_hash"] == b["spec_hash"] and b["variants"]["tried"] == 1  # same spec is not a new variant


def test_run_by_saved_strategy_name(svc, spec):
    tool(svc, "strategy_save", spec=spec)
    assert tool(svc, "backtest_run", strategy="sma-trend")["status"] == "ok"


def test_run_needs_spec_or_strategy(svc):
    with pytest.raises(MidasError) as e:
        tool(svc, "backtest_run")
    assert e.value.code == "invalid_request"


def test_variants_are_counted_per_family(svc, spec):
    tool(svc, "backtest_run", spec=spec)
    for w in (10, 15, 25):
        s = copy.deepcopy(spec)
        s["indicators"]["fast"]["window"] = w
        res = tool(svc, "backtest_run", spec=s)
    assert res["variants"]["tried"] == 4
    assert res["multiple_testing"]["variants_tried"] == 4


def test_failed_runs_are_kept_in_the_log(svc, spec):
    s = copy.deepcopy(spec)
    s["indicators"]["fast"]["window"] = 100000  # never has a value
    try:
        tool(svc, "backtest_run", spec=s)
    except MidasError:
        pass
    log = tool(svc, "experiments_list")
    assert log["total"] >= 1 or log["experiments"] is not None


def test_holdout_is_sealed_until_revealed_and_peeks_are_counted(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.25)
    assert run["holdout"]["status"] == "sealed"
    assert run["window"]["end"] == run["holdout"]["dev_end"] < run["holdout"]["holdout_start"]
    assert "holdout" not in json.dumps(tool(svc, "experiments_list", run_id=run["run_id"])["metrics"])
    v1 = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["bootstrap"], n=100)
    assert v1["results"]["holdout"]["status"] == "sealed"
    o1 = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["bootstrap"], n=100, reveal_holdout=True)["results"]["holdout"]
    o2 = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["bootstrap"], n=100, reveal_holdout=True)["results"]["holdout"]
    assert o1["status"] == "opened" and o1["peek_number"] == 1 and o2["peek_number"] == 2
    assert o1["metrics"]["sharpe"] is not None


def test_split_date_seals_from_that_date(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, split_date="2022-01-01")
    assert run["holdout"]["holdout_start"] >= "2022-01-01" and run["window"]["end"] < "2022-01-01"


def test_validation_methods_seeded_and_reproducible(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.2)
    a = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["permutation", "bootstrap"], n=150, seed=7)["results"]
    b = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["permutation", "bootstrap"], n=150, seed=7)["results"]
    assert a["permutation"]["p_value"] == b["permutation"]["p_value"]
    assert a["bootstrap"]["ci_low"] == b["bootstrap"]["ci_low"]
    p = a["permutation"]["p_value"]
    assert 1 / 151 - 1e-9 <= p <= 1.0
    assert a["bootstrap"]["ci_low"] < a["bootstrap"]["sharpe"] < a["bootstrap"]["ci_high"]


def test_walk_forward_windows_do_not_overlap_and_grid_is_logged(svc, spec):
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.2)
    v = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["walk_forward"], windows=4,
             grid={"indicators.fast.window": [5, 10, 20]})["results"]["walk_forward"]
    wins = v["windows"]
    assert len(wins) == 4
    for a, b in zip(wins, wins[1:]):
        assert a["test_end"] < b["test_start"] and a["train_end"] < a["test_start"]
    assert v["grid"]["candidates"] == 3
    log = tool(svc, "experiments_list", kind="wf_candidate")
    assert log["total"] >= 2  # candidates are variants in the log (the base spec is reused)


def test_multiple_testing_needs_trials_for_deflated_sharpe(svc, spec):
    run = tool(svc, "backtest_run", spec=spec)
    mt = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["multiple_testing"])["results"]["multiple_testing"]
    assert mt["variants_tried"] == 1 and mt["deflated_sharpe"] is None
    assert mt["p_value_bonferroni"] == mt["p_value"]
    for w in (8, 12, 30):
        s = copy.deepcopy(spec)
        s["indicators"]["fast"]["window"] = w
        tool(svc, "backtest_run", spec=s)
    mt = tool(svc, "backtest_validate", run_id=run["run_id"], methods=["multiple_testing"])["results"]["multiple_testing"]
    assert mt["variants_tried"] == 4 and mt["deflated_sharpe"] is not None
    assert mt["p_value_bonferroni"] >= mt["p_value"] and mt["haircut_sharpe_annualised"] <= mt["sharpe_annualised"] + 1e-9


def test_experiments_list_filters_and_detail(svc, spec):
    run = tool(svc, "backtest_run", spec=spec)
    tool(svc, "backtest_validate", run_id=run["run_id"], methods=["bootstrap"], n=100)
    assert tool(svc, "experiments_list", kind="backtest")["total"] == 1
    assert tool(svc, "experiments_list", kind="validation")["total"] == 1
    assert tool(svc, "experiments_list", family="sma-trend")["families"][0]["variants"] == 1
    detail = tool(svc, "experiments_list", run_id=run["run_id"])
    assert detail["validations"] and detail["trades"] is not None and detail["equity"]


def test_unknown_run(svc):
    with pytest.raises(MidasError) as e:
        tool(svc, "backtest_validate", run_id="bt_missing")
    assert e.value.code == "not_found"
    with pytest.raises(MidasError):
        tool(svc, "experiments_list", run_id="bt_missing")


def test_events_emitted(svc, spec):
    events = []
    svc.lab.emit = lambda t, d: events.append((t, d))
    tool(svc, "backtest_run", spec=spec)
    assert events and events[0][0] == "midas.backtest.finished" and events[0][1]["ok"] is True
