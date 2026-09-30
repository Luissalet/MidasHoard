"""Indicators shared by the rule language and the strategy spec.

Every function maps a pandas Series to a Series on the same index, using only the present and the past of each
bar (rolling/expanding windows, never a centred window or a negative shift): a value at bar t never depends on
bar t+1. Percent-valued indicators (roc, vol, drawdown, yoy, pctb) return percent, e.g. -20.0 for a 20% drawdown.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import pandas as pd

MAX_WINDOW = 5000


def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).mean()


def ema(s: pd.Series, window: int) -> pd.Series:
    return s.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(s: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's relative strength index, 0-100."""
    delta = s.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    avg_up = up.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_down = down.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_up / avg_down.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(~((avg_down == 0.0) & avg_up.notna()), 100.0)
    return out


def roc(s: pd.Series, window: int) -> pd.Series:
    """Rate of change over ``window`` bars, in percent."""
    return (s / s.shift(window) - 1.0) * 100.0


def change(s: pd.Series, window: int) -> pd.Series:
    return s - s.shift(window)


def lag(s: pd.Series, window: int) -> pd.Series:
    return s.shift(window)


def rolling_vol(s: pd.Series, window: int, periods_per_year: float = 252.0) -> pd.Series:
    """Annualised standard deviation of simple returns over ``window`` bars, in percent."""
    return s.pct_change().rolling(window, min_periods=window).std() * math.sqrt(periods_per_year) * 100.0


def zscore(s: pd.Series, window: int) -> pd.Series:
    mean = s.rolling(window, min_periods=window).mean()
    std = s.rolling(window, min_periods=window).std()
    return (s - mean) / std.replace(0.0, np.nan)


def bollinger(s: pd.Series, window: int = 20, k: float = 2.0, band: str = "mid") -> pd.Series:
    mid = sma(s, window)
    std = s.rolling(window, min_periods=window).std()
    upper, lower = mid + k * std, mid - k * std
    if band == "upper":
        return upper
    if band == "lower":
        return lower
    if band == "pctb":
        return (s - lower) / (upper - lower).replace(0.0, np.nan) * 100.0
    if band == "bandwidth":
        return (upper - lower) / mid * 100.0
    return mid


def rmax(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).max()


def rmin(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).min()


def drawdown(s: pd.Series) -> pd.Series:
    """Percent below the running maximum (0 at a new high, negative below)."""
    return (s / s.cummax() - 1.0) * 100.0


def yoy(s: pd.Series) -> pd.Series:
    """Percent change against the last observation on or before the same calendar day a year earlier."""
    if s.empty:
        return s.copy()
    prior = s.reindex(s.index - pd.DateOffset(years=1), method="ffill")
    prior.index = s.index
    out = (s / prior - 1.0) * 100.0
    # Before the series has a full year behind it the "prior" is just the first observation: not a year-on-year figure.
    out[s.index < s.index[0] + pd.DateOffset(years=1)] = np.nan
    return out


def pct_returns(s: pd.Series, kind: str = "simple") -> pd.Series:
    if kind == "log":
        return np.log(s / s.shift(1))
    return s.pct_change()


# ---------------------------------------------------------------------------------------------------------------
# Declarative table used by the strategy spec validator/engine: type -> parameters and implementation.
# ---------------------------------------------------------------------------------------------------------------

def _p(kind: str, default: Any = None, low: float | None = None, high: float | None = None, choices: tuple | None = None) -> dict[str, Any]:
    return {"type": kind, "default": default, "low": low, "high": high, "choices": choices}


IndicatorFn = Callable[[pd.Series, dict[str, Any], float], pd.Series]

INDICATOR_DEFS: dict[str, dict[str, Any]] = {
    "sma": {"params": {"window": _p("int", None, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: sma(s, p["window"]),
            "doc": "Simple moving average of the field."},
    "ema": {"params": {"window": _p("int", None, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: ema(s, p["window"]),
            "doc": "Exponential moving average (span = window)."},
    "rsi": {"params": {"window": _p("int", 14, 2, MAX_WINDOW)}, "fn": lambda s, p, ppy: rsi(s, p["window"]),
            "doc": "Wilder RSI, 0-100."},
    "roc": {"params": {"window": _p("int", None, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: roc(s, p["window"]),
            "doc": "Momentum: percent change over window bars (alias: momentum)."},
    "momentum": {"params": {"window": _p("int", None, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: roc(s, p["window"]),
                 "doc": "Alias of roc."},
    "returns": {"params": {"window": _p("int", 1, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: roc(s, p["window"]),
                "doc": "Percent return over window bars (window 1 = daily return)."},
    "vol": {"params": {"window": _p("int", 20, 2, MAX_WINDOW)}, "fn": lambda s, p, ppy: rolling_vol(s, p["window"], ppy),
            "doc": "Rolling annualised volatility, percent."},
    "zscore": {"params": {"window": _p("int", None, 2, MAX_WINDOW)}, "fn": lambda s, p, ppy: zscore(s, p["window"]),
               "doc": "Distance from the rolling mean in rolling standard deviations."},
    "bollinger": {"params": {"window": _p("int", 20, 2, MAX_WINDOW), "k": _p("float", 2.0, 0.1, 10.0),
                             "band": _p("str", "mid", choices=("upper", "lower", "mid", "pctb", "bandwidth"))},
                  "fn": lambda s, p, ppy: bollinger(s, p["window"], p["k"], p["band"]),
                  "doc": "Bollinger band value: band = upper | lower | mid | pctb | bandwidth."},
    "rolling_max": {"params": {"window": _p("int", None, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: rmax(s, p["window"]),
                    "doc": "Highest value of the field over window bars (Donchian high)."},
    "rolling_min": {"params": {"window": _p("int", None, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: rmin(s, p["window"]),
                    "doc": "Lowest value of the field over window bars (Donchian low)."},
    "drawdown": {"params": {}, "fn": lambda s, p, ppy: drawdown(s), "doc": "Percent below the running maximum."},
    "lag": {"params": {"window": _p("int", 1, 1, MAX_WINDOW)}, "fn": lambda s, p, ppy: lag(s, p["window"]),
            "doc": "The field's value window bars ago."},
}
