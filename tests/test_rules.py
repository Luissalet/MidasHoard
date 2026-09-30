from __future__ import annotations

import pytest

from conftest import make_snapshot
from midas_hoard import rules
from midas_hoard.errors import MidasError


def evaluator(**series):
    snaps = {k: make_snapshot(f"snp_{k}", v, symbol=k) for k, v in series.items()}
    return rules.Evaluator(lambda sym: snaps[sym])


def test_validate_examples():
    for src in ["close(aapl.us) < 150", "yoy(CPIAUCSL) > 4", "drawdown(^spx) < -20", "sma(x,50) < sma(x,200)",
                "close(a) < 10 and close(b) > 3", "not (close(a) > 1)", "cross_below(close(a), sma(a, 5))"]:
        res = rules.validate(src)
        assert res["ok"], (src, res)


@pytest.mark.parametrize("src", ["close(a) <", "close(a) << 3", "foo(a) > 1", "close(a", "1 +", "__import__('os')", "close(a) > 1; x"])
def test_validate_rejects_bad_syntax(src):
    res = rules.validate(src)
    assert not res["ok"] and res["issues"][0]["message"]


def test_no_eval_surface():
    res = rules.validate("__import__('os').system('x')")
    assert not res["ok"]


def test_validate_reports_symbols_and_functions():
    res = rules.validate("close(aapl.us) < sma(aapl.us, 50) and yoy(CPIAUCSL) > 4")
    assert res["symbols"] == ["aapl.us", "CPIAUCSL"] and {"close", "sma", "yoy"} <= set(res["functions"])


def test_tripped_and_clear():
    ev = evaluator(a=[10, 11, 12, 9])
    assert ev.evaluate("close(a) < 10")["state"] == "tripped"
    assert ev.evaluate("close(a) > 10")["state"] == "clear"  # 9 > 10 is false
    assert evaluator(a=[10, 11, 12, 13]).evaluate("close(a) < 10")["state"] == "clear"


def test_terms_record_operands():
    res = evaluator(a=[10, 11, 12, 9]).evaluate("close(a) < 10")
    term = res["terms"][0]
    assert term["lhs"] == 9.0 and term["rhs"] == 10.0 and res["data_dates"]["a"]


def test_percent_indicators_are_percent():
    ev = evaluator(a=[100.0] * 300 + [110.0] * 10)
    assert abs(ev.number("yoy(a)")["value"] - 10.0) < 1e-6
    assert abs(ev.number("roc(a, 10)")["value"] - 10.0) < 1e-6
    dd = evaluator(a=[100, 120, 90]).number("drawdown(a)")["value"]
    assert abs(dd - (-25.0)) < 1e-6


def test_sma_rsi_values():
    ev = evaluator(a=[1, 2, 3, 4, 5])
    assert ev.number("sma(a, 5)")["value"] == 3.0
    up = evaluator(a=list(range(1, 40)))
    assert up.number("rsi(a, 14)")["value"] > 99


def test_cross_above():
    ev = evaluator(a=[5, 5, 5, 5, 9], b=[6, 6, 6, 6, 6])
    assert ev.evaluate("cross_above(close(a), close(b))")["state"] == "tripped"
    assert evaluator(a=[5, 5, 5, 5, 5], b=[6, 6, 6, 6, 6]).evaluate("cross_above(close(a), close(b))")["state"] == "clear"


def test_no_data_when_window_too_long():
    res = evaluator(a=[1, 2, 3]).evaluate("sma(a, 50) > 1")
    assert res["state"] in ("no_data", "error")


def test_unknown_symbol_is_an_error_state_or_raises():
    def resolve(sym):
        raise MidasError("not_found", f"no snapshot for {sym}")
    ev = rules.Evaluator(resolve)
    try:
        res = ev.evaluate("close(zzz) > 1")
        assert res["state"] in ("error", "no_data")
    except MidasError as e:
        assert e.code == "not_found"


def test_quote_symbol_round_trips():
    for sym in ["aapl.us", "^spx", "EXR/D.USD.EUR.SP00.A", "bitcoin:eur", "fake.up"]:
        src = f"close({rules.quote_symbol(sym)}) > 1"
        assert rules.validate(src)["ok"], src
        assert rules.validate(src)["symbols"] == [sym]
