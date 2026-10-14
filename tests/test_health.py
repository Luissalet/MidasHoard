"""Data health: computed on the raw series before any metric, nulls reported and never filled."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from conftest import make_snapshot, tool
from midas_hoard import health
from midas_hoard.errors import MidasError
from midas_hoard.strategy import EXAMPLE_SPEC


def walk(n=500, seed=1, vol=0.01):
    rng = np.random.default_rng(seed)
    return 100 * np.cumprod(1 + rng.normal(0.0004, vol, n))


def test_clean_series_passes_every_gate():
    h = health.snapshot_health(make_snapshot("a", walk(500)))
    assert h["status"] == "pass" and h["issues"] == []
    m = h["metrics"]
    assert m["null_ratio"] == 0 and m["zero_ratio"] == 0 and m["coverage_ratio"] == pytest.approx(1.0) and m["long_gaps"] == 0
    assert set(h["gates"]) == {"min_rows", "coverage_ratio", "null_ratio", "zero_ratio", "outlier_ratio_3sigma", "long_gaps"}
    assert h["gates"]["coverage_ratio"]["threshold"] == {"warn_below": 0.95, "fail_below": 0.90}  # the thresholds travel with the result


def test_nulls_are_measured_not_filled():
    closes = walk(400)
    closes[100:140] = np.nan  # 10 % of the cells
    snap = make_snapshot("n", closes)
    before = snap.df.copy()
    h = health.snapshot_health(snap)
    assert h["metrics"]["null_ratio"] == pytest.approx(40 * 4 / (400 * 4), abs=0.011) or h["metrics"]["null_ratio"] > 0.09
    assert h["metrics"]["null_closes"] == 40
    assert h["gates"]["null_ratio"]["status"] in ("warn", "fail")
    assert h["metrics"]["coverage_ratio"] == pytest.approx(360 / 400, abs=0.01)
    pd.testing.assert_frame_equal(snap.df, before)  # the series itself is untouched: no fillna(0), no dropna in place


def test_zeros_are_counted_separately_from_nulls():
    closes = walk(300)
    closes[50:80] = 0.0
    h = health.snapshot_health(make_snapshot("z", closes))
    assert h["metrics"]["zero_ratio"] == pytest.approx(30 / 300, abs=1e-9) and h["metrics"]["null_ratio"] == 0
    assert h["gates"]["zero_ratio"]["status"] == "fail" and h["status"] == "fail"


def test_gap_lowers_coverage_and_is_listed():
    snap = make_snapshot("g", walk(520))
    df = snap.df.drop(snap.df.index[200:260])  # 60 business days vanish
    snap.df = df
    h = health.snapshot_health(snap)
    assert h["metrics"]["coverage_ratio"] == pytest.approx(460 / 520, abs=0.01) and h["gates"]["coverage_ratio"]["status"] == "fail"
    assert h["metrics"]["long_gaps"] == 1 and h["gaps"][0]["days"] >= 80 and h["gates"]["long_gaps"]["status"] == "fail"
    assert any("coverage" in i for i in h["issues"])


def test_coverage_between_90_and_95_warns():
    snap = make_snapshot("w", walk(500))
    snap.df = snap.df.drop(snap.df.index[::13][:38])  # about 7.5 % missing, scattered
    h = health.snapshot_health(snap)
    assert 0.90 <= h["metrics"]["coverage_ratio"] < 0.95 and h["gates"]["coverage_ratio"]["status"] == "warn" and h["status"] in ("warn", "fail")


def test_three_sigma_outliers_are_dated():
    closes = walk(500, vol=0.005)
    closes[250:] *= 1.9  # a single +90 % jump on one day
    h = health.snapshot_health(make_snapshot("o", closes))
    assert h["metrics"]["outlier_count"] >= 1 and h["outliers"][0]["return"] == pytest.approx(0.9, abs=0.05)
    assert h["outliers"][0]["date"] == make_snapshot("o", closes).df.index[250].strftime("%Y-%m-%d")
    assert h["gates"]["outlier_ratio_3sigma"]["value"] is not None


def test_a_missing_close_does_not_create_a_zero_return_outlier():
    closes = walk(400)
    closes[200] = np.nan
    h = health.snapshot_health(make_snapshot("m", closes))
    assert all(abs(o["return"]) > 0.01 for o in h["outliers"])  # the 2-day return across the hole is a real return, never a 0 or a -100 %
    assert h["metrics"]["null_closes"] == 1


def test_window_is_respected_and_short_series_fail():
    snap = make_snapshot("s", walk(300))
    h = health.snapshot_health(snap, start="2020-03-01", end="2020-06-30")
    assert h["window"]["start"] >= "2020-03-01" and h["window"]["end"] <= "2020-06-30" and h["rows"] < 300
    tiny = health.snapshot_health(make_snapshot("t", walk(10)))
    assert tiny["status"] == "fail" and tiny["gates"]["min_rows"]["status"] == "fail"
    empty = health.series_health(snap.df.iloc[0:0], snap.meta)
    assert empty["status"] == "fail"


def test_weekly_and_monthly_series_use_their_own_calendar():
    idx = pd.date_range("2018-01-31", periods=48, freq="ME")
    snap = make_snapshot("mth", walk(48), frequency="M", ppy=12)
    assert health.snapshot_health(snap)["metrics"]["coverage_ratio"] == pytest.approx(1.0)
    snap.df = snap.df.drop(snap.df.index[10:20])
    assert health.snapshot_health(snap)["metrics"]["coverage_ratio"] == pytest.approx(38 / 48, abs=0.02)
    assert len(idx) == 48


def test_weekend_trading_series_are_judged_on_calendar_days():
    snap = make_snapshot("c", walk(365 * 2))
    snap.df = pd.DataFrame({"close": snap.df["close"].to_numpy()[:365]}, index=pd.date_range("2022-01-01", periods=365, freq="D"))
    h = health.snapshot_health(snap)
    assert h["metrics"]["coverage_ratio"] == pytest.approx(1.0) and h["status"] == "pass"


def test_summary_takes_the_worst_series():
    good = health.snapshot_health(make_snapshot("a", walk(300)))
    z = walk(300)
    z[10:40] = 0.0
    bad = health.snapshot_health(make_snapshot("b", z))
    s = health.summarize([good, bad])
    assert s["status"] == "fail" and s["series"] == 2 and any(i["series"] == "b" for i in s["issues"]) and s["min_coverage"] == pytest.approx(1.0, abs=0.01)
    assert health.summarize([])["status"] == "unknown"


# ---------------------------------------------------------------------------------------------- through the app
CSV_HEADER = "Date,Close\n"


def csv_snapshot(svc, symbol, rows):
    text = CSV_HEADER + "\n".join(f"{d.strftime('%Y-%m-%d')},{c:.4f}" for d, c in rows)
    meta = tool(svc, "market_fetch", provider="csv", symbol=symbol, csv_text=text, currency="USD", mapping={"date": "Date", "close": "Close"})
    return meta["snapshot"]["id"]


def test_data_health_tool_on_a_snapshot_with_holes(svc):
    idx = pd.bdate_range("2019-01-01", periods=700)
    closes = walk(700)
    keep = [i for i in range(700) if not (150 <= i < 330)]  # 180 business days are missing
    sid = csv_snapshot(svc, "holey", [(idx[i], closes[i]) for i in keep])
    res = tool(svc, "data_health", snapshot_ids=[sid])
    s = res["series"][0]
    assert res["summary"]["status"] == "fail" and s["metrics"]["coverage_ratio"] < 0.9 and s["metrics"]["long_gaps"] == 1
    assert res["summary"]["min_coverage"] == s["metrics"]["coverage_ratio"]


def test_low_coverage_caps_the_report_decision_at_investigate(svc):
    idx = pd.bdate_range("2019-01-01", periods=900)
    closes = walk(900, vol=0.01)
    keep = [i for i in range(900) if not (300 <= i < 420)]
    sid = csv_snapshot(svc, "holes2", [(idx[i], closes[i]) for i in keep])
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = [sid]
    run = tool(svc, "backtest_run", spec=spec, holdout_fraction=0.3)
    assert run["data_health"]["summary"]["status"] == "fail" and run["decision"]["decision"] == "investigate"
    assert "health_fail" in run["decision"]["because"] or "low_coverage" in run["decision"]["because"]
    tool(svc, "backtest_validate", run_id=run["run_id"], windows=4, n=150)
    rep = svc.lab.run_report(run["run_id"])
    assert rep["decision"]["decision"] == "investigate"
    gates = {g["id"]: g for s in rep["sections"] if s["id"] == "gates" for g in s["gates"]}
    assert gates["data_health"]["status"] == "fail" and "coverage" in gates["data_health"]["text"]
    # the health of the data a run used can be asked later, on the run's own window
    again = tool(svc, "data_health", run_id=run["run_id"])
    assert again["summary"]["status"] == "fail" and again["run_id"] == run["run_id"]


def test_data_health_by_strategy_spec_and_errors(svc, fetch):
    sid = fetch("fake.up")["snapshot"]["id"]
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = [sid]
    assert tool(svc, "data_health", spec=spec)["summary"]["status"] in ("pass", "warn")
    tool(svc, "strategy_save", spec=spec)
    assert tool(svc, "data_health", strategy="sma-trend")["summary"]["series"] == 1
    with pytest.raises(MidasError) as e:
        tool(svc, "data_health")
    assert e.value.code == "invalid_request"
    with pytest.raises(MidasError):
        tool(svc, "data_health", snapshot_ids=["snp_missing"])


def test_nulls_in_the_universe_are_flagged_in_the_run_warnings(svc, fetch):
    from midas_hoard.snapshots import Snapshot
    sid = fetch("fake.up")["snapshot"]["id"]
    snap = svc.lab.load(sid)
    df = snap.df.copy()
    df.iloc[500:505, df.columns.get_loc("close")] = np.nan
    svc.lab._cache[sid] = Snapshot(snap.meta, df)
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = [sid]
    run = tool(svc, "backtest_run", spec=spec)
    assert any("null close" in w for w in run["warnings"])
    m = run["data_health"]["series"][0]["metrics"]
    assert m["null_closes"] == 5 and m["null_ratio"] > 0


@pytest.mark.parametrize("freq, per, rule", [("W", 52, "W-FRI"), ("Q", 20, "QE"), ("Y", 10, "YE")])
def test_slower_calendars_count_one_value_per_period(freq, per, rule):
    snap = make_snapshot("p", walk(per), ppy=per)
    snap.df.index = pd.date_range("2005-12-31" if freq == "Y" else "2018-03-31", periods=per, freq=rule)
    snap.meta["frequency"] = freq
    assert health.snapshot_health(snap)["metrics"]["coverage_ratio"] == pytest.approx(1.0)
    snap.df = snap.df.drop(snap.df.index[3:6])
    assert health.snapshot_health(snap)["metrics"]["coverage_ratio"] == pytest.approx((per - 3) / per, abs=0.03)
