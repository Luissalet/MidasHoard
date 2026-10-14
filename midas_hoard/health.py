"""Data health: computed on the raw frozen series *before* any metric is interpreted.

Per series and window: share of null cells, share of exact zeros, coverage by date (days with a valid value / days
expected), 3-sigma return outliers and long gaps. Nulls are measured, never filled: a missing value is reported as
missing. The thresholds used are returned with the result so a verdict can always be traced to the gate that produced it.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from .snapshots import Snapshot

PRICE_COLUMNS = ("open", "high", "low", "close")
COVERAGE_BASIS = ("valid close dates divided by the dates expected in the window (business days for daily series with no weekend rows, "
                  "calendar days for daily series that trade every day, one per period otherwise); holidays are not removed, so exchange "
                  "calendars lose about 3-4 % by construction")
OUTLIER_BASIS = "returns more than 3 standard deviations from their window mean, divided by the number of returns"

# status of a metric: pass < warn < fail. Values are fractions (0.05 = 5 %).
THRESHOLDS: dict[str, dict[str, float]] = {
    "coverage_ratio": {"warn_below": 0.95, "fail_below": 0.90},
    "null_ratio": {"warn_above": 0.02, "fail_above": 0.10},
    "zero_ratio": {"warn_above": 0.01, "fail_above": 0.05},
    "outlier_ratio_3sigma": {"warn_above": 0.02},
    "long_gap_factor": {"warn_above": 1.0, "fail_above": 4.0},  # longest gap / the long-gap threshold
    "min_rows": {"fail_below": 20},
}
ORDER = {"pass": 0, "warn": 1, "fail": 2}


def _worse(a: str, b: str) -> str:
    return a if ORDER[a] >= ORDER[b] else b


def _status_hi(value: Optional[float], rule: dict[str, float]) -> str:
    if value is None:
        return "pass"
    if "fail_above" in rule and value > rule["fail_above"]:
        return "fail"
    if "warn_above" in rule and value > rule["warn_above"]:
        return "warn"
    return "pass"


def _status_lo(value: Optional[float], rule: dict[str, float]) -> str:
    if value is None:
        return "pass"
    if "fail_below" in rule and value < rule["fail_below"]:
        return "fail"
    if "warn_below" in rule and value < rule["warn_below"]:
        return "warn"
    return "pass"


def _expected_dates(index: pd.DatetimeIndex, first: pd.Timestamp, last: pd.Timestamp) -> pd.DatetimeIndex:
    """The dates a daily series should have between ``first`` and ``last``: every day if it trades on weekends, else business days."""
    weekend = int(np.sum(index.dayofweek >= 5)) if len(index) else 0
    if len(index) and weekend / len(index) > 0.05:  # trades every day (crypto)
        return pd.date_range(first, last, freq="D")
    return pd.bdate_range(first, last)


def _per_period_coverage(index: pd.DatetimeIndex, valid: pd.Series, frequency: str, first: pd.Timestamp, last: pd.Timestamp) -> tuple[int, int]:
    """(periods with a valid value, periods expected) for weekly and slower series; daily series use dates directly."""
    f = (frequency or "D").upper()[:1]
    freq = {"W": "W-FRI", "M": "M", "Q": "Q", "Y": "Y", "A": "Y"}[f]
    expected = pd.period_range(first, last, freq=freq)
    got = pd.PeriodIndex(valid.index[valid.to_numpy()], freq=freq).unique()
    return int(len(got.intersection(expected))), int(len(expected))


def series_health(df: pd.DataFrame, meta: dict[str, Any], *, start: Optional[str] = None, end: Optional[str] = None,
                  thresholds: Optional[dict[str, dict[str, float]]] = None) -> dict[str, Any]:
    """Health of one series over [start, end] (default: its whole range). Never mutates or fills ``df``."""
    th = {**THRESHOLDS, **(thresholds or {})}
    frame = df
    if start:
        frame = frame[frame.index >= pd.Timestamp(start)]
    if end:
        frame = frame[frame.index <= pd.Timestamp(end)]
    out: dict[str, Any] = {"snapshot": meta.get("id"), "symbol": meta.get("symbol"), "provider": meta.get("provider"),
                           "frequency": meta.get("frequency"), "currency": meta.get("currency") or None, "rows": int(len(frame)),
                           "window": {"start": start, "end": end}}
    if "close" not in frame.columns or len(frame) == 0:
        out.update({"status": "fail", "metrics": {}, "gates": {"min_rows": {"status": "fail", "value": int(len(frame)), "threshold": th["min_rows"]}},
                    "issues": ["the series has no 'close' values in this window"], "outliers": [], "gaps": []})
        return out
    first, last = frame.index[0], frame.index[-1]
    out["window"] = {"start": first.strftime("%Y-%m-%d"), "end": last.strftime("%Y-%m-%d")}
    price_cols = [c for c in PRICE_COLUMNS if c in frame.columns]
    cells = frame[price_cols]
    n_cells = int(cells.size)
    null_cells = int(cells.isna().sum().sum())
    zero_cells = int((cells == 0).sum().sum())
    valid_close = frame["close"].notna()
    close = frame["close"].dropna()
    # coverage by date
    f = (meta.get("frequency") or "D").upper()[:1]
    if f == "D" or f not in ("W", "M", "Q", "Y", "A"):
        expected = _expected_dates(frame.index, first, last)
        have = int(len(expected.intersection(frame.index[valid_close.to_numpy()])))
        n_expected = int(len(expected))
    else:
        have, n_expected = _per_period_coverage(frame.index, valid_close, meta.get("frequency") or "D", first, last)
    coverage = (have / n_expected) if n_expected else None
    # outliers (on valid closes only: a missing close is not turned into a zero return)
    rets = close.pct_change().dropna()
    rets = rets[np.isfinite(rets)]
    outliers: list[dict[str, Any]] = []
    out_ratio: Optional[float] = None
    out_count = 0
    if len(rets) >= 10 and float(rets.std(ddof=1)) > 0:
        z = (rets - rets.mean()) / rets.std(ddof=1)
        bad = rets[z.abs() > 3.0]
        out_count = int(len(bad))
        out_ratio = float(out_count / len(rets))
        outliers = [{"date": ts.strftime("%Y-%m-%d"), "return": float(v), "sigma": float(z[ts])} for ts, v in bad.head(10).items()]
    # long gaps between consecutive valid dates
    gaps: list[dict[str, Any]] = []
    longest, threshold_days = 0, None
    if len(close) >= 3:
        spans = pd.Series(close.index[1:] - close.index[:-1]).dt.days.to_numpy()
        median = float(np.median(spans))
        threshold_days = max(7.0, 3.0 * median)
        for i, d in enumerate(spans):
            if d > threshold_days:
                gaps.append({"from": close.index[i].strftime("%Y-%m-%d"), "to": close.index[i + 1].strftime("%Y-%m-%d"), "days": int(d)})
        longest = int(spans.max())
    gap_factor = (longest / threshold_days) if threshold_days else None
    metrics = {
        "null_ratio": (null_cells / n_cells) if n_cells else None, "zero_ratio": (zero_cells / n_cells) if n_cells else None,
        "coverage_ratio": coverage, "valid_dates": have, "expected_dates": n_expected,
        "outlier_ratio_3sigma": out_ratio, "outlier_count": out_count,
        "long_gaps": len(gaps), "longest_gap_days": longest or None, "long_gap_threshold_days": threshold_days,
        "null_closes": int((~valid_close).sum()),
    }
    gates = {
        "min_rows": {"status": "fail" if len(close) < th["min_rows"]["fail_below"] else "pass", "value": int(len(close)), "threshold": th["min_rows"]},
        "coverage_ratio": {"status": _status_lo(coverage, th["coverage_ratio"]), "value": coverage, "threshold": th["coverage_ratio"]},
        "null_ratio": {"status": _status_hi(metrics["null_ratio"], th["null_ratio"]), "value": metrics["null_ratio"], "threshold": th["null_ratio"]},
        "zero_ratio": {"status": _status_hi(metrics["zero_ratio"], th["zero_ratio"]), "value": metrics["zero_ratio"], "threshold": th["zero_ratio"]},
        "outlier_ratio_3sigma": {"status": _status_hi(out_ratio, th["outlier_ratio_3sigma"]), "value": out_ratio, "threshold": th["outlier_ratio_3sigma"]},
        "long_gaps": {"status": _status_hi(gap_factor, th["long_gap_factor"]), "value": longest or None, "threshold_days": threshold_days},
    }
    status = "pass"
    issues: list[str] = []
    for name, g in gates.items():
        status = _worse(status, g["status"])
        if g["status"] != "pass":
            issues.append(_issue_text(name, g, metrics))
    out.update({"status": status, "metrics": metrics, "gates": gates, "issues": issues, "outliers": outliers, "gaps": gaps[:10],
                "basis": {"coverage": COVERAGE_BASIS, "outliers": OUTLIER_BASIS}})
    return out


def _issue_text(name: str, gate: dict[str, Any], m: dict[str, Any]) -> str:
    v = gate.get("value")
    pct = lambda x: "no data" if x is None else f"{x * 100:.1f} %"  # noqa: E731
    if name == "min_rows":
        return f"only {v} valid rows: too few to interpret"
    if name == "coverage_ratio":
        return f"coverage {pct(v)} ({m['valid_dates']} of {m['expected_dates']} expected dates)"
    if name == "null_ratio":
        return f"{pct(v)} of the price cells are null"
    if name == "zero_ratio":
        return f"{pct(v)} of the price cells are exactly zero"
    if name == "outlier_ratio_3sigma":
        return f"{pct(v)} of the returns are beyond 3 sigma ({m['outlier_count']} of them)"
    return f"{m['long_gaps']} gap(s) longer than {gate.get('threshold_days'):.0f} days; the longest is {v} days"


def snapshot_health(snap: Snapshot, *, start: Optional[str] = None, end: Optional[str] = None,
                    thresholds: Optional[dict[str, dict[str, float]]] = None) -> dict[str, Any]:
    return series_health(snap.df, snap.meta, start=start, end=end, thresholds=thresholds)


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    """One verdict for a set of series: the worst status, the lowest coverage and every issue with its series."""
    if not items:
        return {"status": "unknown", "series": 0, "min_coverage": None, "issues": [], "note": "no series were checked"}
    status = "pass"
    issues: list[dict[str, str]] = []
    cov = [i["metrics"].get("coverage_ratio") for i in items if i.get("metrics")]
    cov = [c for c in cov if c is not None]
    for i in items:
        status = _worse(status, i["status"])
        for text in i.get("issues", []):
            issues.append({"series": i.get("symbol") or i.get("snapshot") or "?", "issue": text})
    return {"status": status, "series": len(items), "min_coverage": min(cov) if cov else None, "issues": issues,
            "thresholds": THRESHOLDS, "note": "Computed on the raw frozen series before any metric; nulls are reported, never filled."}


def finite(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x
