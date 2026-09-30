from __future__ import annotations

import copy

import numpy as np
import pytest

from conftest import make_snapshot
from midas_hoard.backtest import run_backtest
from midas_hoard.errors import MidasError
from midas_hoard.strategy import EXAMPLE_SPEC, spec_hash, validate_spec


def norm(spec, ids=("snp_t",)):
    res = validate_spec(spec, snapshot_exists=lambda s: s in ids)
    assert res["ok"], res["issues"]
    return res["normalized"]


def jump_spec(**over):
    spec = {"name": "jump", "universe": ["snp_t"], "indicators": {"r": {"type": "roc", "window": 1}},
            "entry": {"all": [{"left": "r", "op": ">", "right": 10}]}, "exit": {"all": [{"left": "r", "op": "<", "right": 1}]},
            "costs_bps": 0, "slippage_bps": 0}
    spec.update(over)
    return spec


def run(spec, snap, **kw):
    return run_backtest(norm(spec, (snap.id,)), lambda i: snap, **kw)


# ---------------------------------------------------------------- no look-ahead
def test_signal_on_bar_t_never_earns_bar_t():
    """A +50% jump on bar 30 triggers the entry rule *at* bar 30's close. The position can only earn from bar 31 on,
    so the jump itself must contribute nothing to the strategy's return."""
    closes = np.full(60, 100.0)
    closes[30:] = 150.0
    snap = make_snapshot("snp_t", closes)
    out = run(jump_spec(), snap)
    assert out.metrics["total_return"] == pytest.approx(0.0, abs=1e-12)
    assert out.net.abs().max() == pytest.approx(0.0, abs=1e-12)
    assert out.metrics["trades"] == 1  # it did enter, just too late to profit


def test_position_earns_the_bar_after_the_signal():
    closes = np.full(60, 100.0)
    closes[30:] = 110.0      # signal bar
    closes[31:] = 121.0      # the bar the position earns
    snap = make_snapshot("snp_t", closes)
    out = run(jump_spec(entry={"all": [{"left": "r", "op": ">", "right": 5}]}, exit={"all": [{"left": "r", "op": "<", "right": -50}]}), snap)
    assert out.metrics["total_return"] == pytest.approx(0.10, abs=1e-9)


def test_truncating_the_future_does_not_change_the_past():
    """Results up to a date must be identical whether or not later data exists (causality)."""
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, 400)))
    full = make_snapshot("snp_t", closes)
    part = make_snapshot("snp_t", closes[:300])
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = ["snp_t"]
    a = run(spec, full)
    b = run(spec, part)
    cut = b.net.index
    assert np.allclose(a.net.loc[cut].values, b.net.values, atol=1e-12)


def test_as_of_cuts_data():
    closes = 100 + np.arange(200.0)
    snap = make_snapshot("snp_t", closes)
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = ["snp_t"]
    out = run(spec, snap, as_of=str(snap.df.index[120].date()))
    assert out.window["bars"] == 121 and out.window["end"] == str(snap.df.index[120].date())


# ---------------------------------------------------------------- mechanics
def test_costs_reduce_return():
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.012, 500)))
    snap = make_snapshot("snp_t", closes)
    base = copy.deepcopy(EXAMPLE_SPEC)
    base["universe"] = ["snp_t"]
    free = run({**base, "costs_bps": 0, "slippage_bps": 0}, snap)
    costly = run({**base, "costs_bps": 50, "slippage_bps": 50}, snap)
    assert costly.metrics["total_return"] < free.metrics["total_return"]
    assert costly.metrics["costs_paid"] > 0 == free.metrics["costs_paid"]


def test_buy_and_hold_equivalent_matches_benchmark():
    closes = 100 * 1.001 ** np.arange(250)
    snap = make_snapshot("snp_t", closes)
    spec = {"name": "always", "universe": ["snp_t"], "indicators": {"c": {"type": "sma", "window": 2}},
            "entry": {"all": [{"left": "c", "op": ">", "right": 0}]}, "exit": {"all": [{"left": "c", "op": "<", "right": -1}]},
            "costs_bps": 0, "slippage_bps": 0}
    out = run(spec, snap)
    # in the market from bar 3 on: a hair below buy & hold from the first bar
    assert 0.9 * out.benchmark["total_return"] < out.metrics["total_return"] <= out.benchmark["total_return"] + 1e-12


def test_metrics_shape_and_drawdown_sign():
    rng = np.random.default_rng(5)
    snap = make_snapshot("snp_t", 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 600))))
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = ["snp_t"]
    m = run(spec, snap).metrics
    for key in ("total_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar", "hit_rate", "turnover_per_year", "exposure", "costs_paid"):
        assert key in m
    assert m["max_drawdown"] <= 0 and 0 <= m["exposure"] <= 1


def test_short_needs_flag():
    closes = np.r_[np.linspace(100, 60, 120), np.linspace(60, 90, 120)]
    snap = make_snapshot("snp_t", closes)
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = ["snp_t"]
    long_only = run(spec, snap)
    shorting = run({**spec, "allow_short": True}, snap)
    assert shorting.metrics["total_return"] != long_only.metrics["total_return"]


# ---------------------------------------------------------------- spec validation
def test_spec_hash_ignores_name_family_note_but_not_parameters():
    a = copy.deepcopy(EXAMPLE_SPEC)
    b = {**a, "name": "other", "family": "x", "note": "hello"}
    c = copy.deepcopy(a)
    c["indicators"]["fast"]["window"] = 21
    assert spec_hash(norm(a, tuple(a["universe"]))) == spec_hash(norm(b, tuple(b["universe"])))
    assert spec_hash(norm(a, tuple(a["universe"]))) != spec_hash(norm(c, tuple(c["universe"])))


@pytest.mark.parametrize("patch,needle", [
    ({"universe": []}, "universe"),
    ({"indicators": {"x": {"type": "nope", "window": 3}}}, "type"),
    ({"entry": {"all": [{"left": "ghost", "op": ">", "right": 1}]}}, "ghost"),
    ({"costs_bps": -5}, "costs"),
    ({"rebalance": "hourly"}, "rebalance"),
    ({"sizing": {"type": "yolo"}}, "sizing"),
])
def test_invalid_specs_report_path_message_and_hint(patch, needle):
    spec = copy.deepcopy(EXAMPLE_SPEC)
    spec["universe"] = ["snp_t"]
    spec.update(patch)
    res = validate_spec(spec, snapshot_exists=lambda s: True)
    assert not res["ok"] and res["issues"]
    issue = res["issues"][0]
    assert issue["message"] and "path" in issue
    assert needle in (issue["path"] + issue["message"]).lower()


def test_unknown_snapshot_is_reported():
    spec = copy.deepcopy(EXAMPLE_SPEC)
    res = validate_spec(spec, snapshot_exists=lambda s: False)
    assert not res["ok"] and "snapshot" in res["issues"][0]["message"].lower()


def test_non_object_spec():
    assert not validate_spec("nope")["ok"]
