"""Series analytics over snapshots: returns, volatility, drawdown, resampling, rebasing, comparison, FX conversion.

Incompatible series (different currency, adjustment or frequency) are never merged silently: the functions here
refuse with ``incompatible_series`` and a hint, or — when the caller asks explicitly — convert or resample and
record exactly what was done in the result.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .errors import MidasError
from .snapshots import Snapshot, provenance

RESAMPLE_RULES = {"W": "W-FRI", "M": "ME", "Q": "QE", "A": "YE"}
FREQ_RANK = {"D": 0, "W": 1, "M": 2, "Q": 3, "A": 4}
RATE_UNITS = ("percent", "%", "percentage points")


def ppy_of(meta: dict[str, Any]) -> float:
    return float(meta.get("periods_per_year") or 252.0)


def cut(df: pd.DataFrame, *, start: Optional[str] = None, end: Optional[str] = None, as_of: Optional[str] = None) -> pd.DataFrame:
    """Point-in-time slice: nothing after ``as_of`` (or ``end``) survives."""
    out = df
    if as_of:
        out = out[out.index <= pd.Timestamp(as_of)]
    if end:
        out = out[out.index <= pd.Timestamp(end)]
    if start:
        out = out[out.index >= pd.Timestamp(start)]
    return out


def resample_frame(df: pd.DataFrame, rule: str, source_freq: str) -> pd.DataFrame:
    """Downsample to W/M/Q/A (last close, first open, max high, min low, summed volume), stamped at the period end."""
    rule = rule.upper()
    if rule not in RESAMPLE_RULES:
        raise MidasError("invalid_request", f"Unknown resample rule '{rule}'.", "Use W, M, Q or A.")
    if FREQ_RANK.get(rule, 0) < FREQ_RANK.get(source_freq, 0):
        raise MidasError("incompatible_series", f"Cannot resample a {source_freq} series up to {rule}: that would invent data.",
                         "Only downsampling is supported (D -> W -> M -> Q -> A).")
    agg: dict[str, str] = {}
    for col in df.columns:
        agg[col] = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}.get(col, "last")
    out = df.resample(RESAMPLE_RULES[rule]).agg(agg)
    if "close" in out.columns:
        out = out[out["close"].notna()]
    return out


def simple_stats(close: pd.Series, ppy: float) -> dict[str, Any]:
    """Headline numbers of a price/level series over its own span."""
    s = close.dropna()
    if len(s) < 2:
        return {"n": int(len(s))}
    rets = s.pct_change().dropna()
    days = (s.index[-1] - s.index[0]).days
    years = days / 365.25
    total = float(s.iloc[-1] / s.iloc[0] - 1.0)
    cagr = float((s.iloc[-1] / s.iloc[0]) ** (1.0 / years) - 1.0) if years >= 0.25 and s.iloc[0] > 0 and s.iloc[-1] > 0 else None
    dd = ind.drawdown(s) / 100.0
    return {
        "n": int(len(s)), "first_date": s.index[0].strftime("%Y-%m-%d"), "last_date": s.index[-1].strftime("%Y-%m-%d"),
        "first": float(s.iloc[0]), "last": float(s.iloc[-1]), "total_return": total, "cagr": cagr,
        "ann_vol": float(rets.std() * math.sqrt(ppy)) if len(rets) > 1 else None, "max_drawdown": float(dd.min()),
        "current_drawdown": float(dd.iloc[-1]), "best_period": float(rets.max()), "worst_period": float(rets.min()),
    }


def _fx_pair(fx: Snapshot) -> tuple[str, str]:
    base, quote = fx.meta.get("extra", {}).get("base"), fx.meta.get("extra", {}).get("quote")
    if base and quote:
        return str(base).upper(), str(quote).upper()
    unit = fx.meta.get("unit", "")
    if " per " in unit:
        q, b = unit.split(" per ", 1)
        return b.strip().upper(), q.strip().upper()
    raise MidasError("incompatible_series", f"Snapshot {fx.id} does not declare a currency pair (base/quote).",
                     "Use an exchange-rate snapshot (eurusd, ECB EXR/D.USD.EUR.SP00.A, DEXUSEU).")


def convert_frame(df: pd.DataFrame, meta: dict[str, Any], fx: Snapshot, target: str, *, as_of: Optional[str] = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Convert price columns to ``target`` with an exchange-rate snapshot (as-of join, backward fill only)."""
    source = (meta.get("currency") or "").upper()
    target = target.upper()
    if not source:
        raise MidasError("incompatible_series", f"Snapshot {meta['id']} declares no currency, so it cannot be converted.",
                         "Re-import it with a declared currency (csv provider) or pick a priced series.")
    if source == target:
        return df, {"converted": False}
    base, quote = _fx_pair(fx)
    if base == source and quote == target:
        direction = "multiply"
    elif base == target and quote == source:
        direction = "divide"
    else:
        raise MidasError("incompatible_series", f"Exchange-rate snapshot {fx.id} is {base}/{quote}; converting {source} to {target} needs that pair.",
                         f"Fetch the {source}/{target} (or {target}/{source}) rate and pass it.")
    rate = fx.df["close"].dropna()
    if as_of:
        rate = rate[rate.index <= pd.Timestamp(as_of)]
    aligned = rate.reindex(df.index.union(rate.index)).ffill().reindex(df.index)
    lag_days = pd.Series(df.index, index=df.index) - pd.Series(rate.index, index=rate.index).reindex(df.index.union(rate.index)).ffill().reindex(df.index)
    factor = aligned if direction == "multiply" else 1.0 / aligned
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] * factor
    before = len(out)
    out = out[out["close"].notna()]
    record = {
        "converted": True, "from": source, "to": target, "fx_snapshot": fx.id, "fx_symbol": fx.meta["symbol"],
        "pair": f"{base}/{quote}", "direction": direction, "rows_dropped_without_rate": before - len(out),
        "max_rate_staleness_days": int(lag_days.dropna().dt.days.max()) if lag_days.notna().any() else None,
    }
    return out, record


def check_compatible(snaps: list[Snapshot], *, allow_incompatible: bool = False, need_same_frequency: bool = True) -> list[str]:
    """Refuse to combine series that differ in currency, adjustment or frequency; return warnings when allowed."""
    warnings: list[str] = []
    problems: list[str] = []
    currencies = {s.meta["currency"] for s in snaps if s.meta["currency"]}
    if len(currencies) > 1:
        problems.append("currencies differ: " + ", ".join(f"{s.meta['symbol']}={s.meta['currency'] or 'none'}" for s in snaps))
    if any(not s.meta["currency"] for s in snaps) and currencies:
        warnings.append("some series declare no currency (indices, rates): they are combined as unit-less levels.")
    if len({s.meta["adjusted"] for s in snaps}) > 1:
        problems.append("adjustment differs (adjusted vs unadjusted prices): " + ", ".join(f"{s.meta['symbol']}={'adj' if s.meta['adjusted'] else 'raw'}" for s in snaps))
    if need_same_frequency and len({s.meta["frequency"] for s in snaps}) > 1:
        problems.append("frequencies differ: " + ", ".join(f"{s.meta['symbol']}={s.meta['frequency']}" for s in snaps))
    if problems and not allow_incompatible:
        raise MidasError("incompatible_series", "Refusing to merge incompatible series: " + "; ".join(problems) + ".",
                         "Convert currency with convert_to + fx, downsample with resample='M', or pass allow_incompatible=true to "
                         "proceed with the mismatch recorded in the result.", problems=problems)
    warnings.extend(f"INCOMPATIBLE (allowed by caller): {p}" for p in problems)
    return warnings


def prepare(snapshot: Snapshot, *, field: str = "close", start: Optional[str] = None, end: Optional[str] = None,
            as_of: Optional[str] = None, convert_to: Optional[str] = None, fx: Optional[Snapshot] = None,
            resample: Optional[str] = None) -> tuple[pd.Series, dict[str, Any]]:
    """One series ready for analysis: sliced point-in-time, converted and/or resampled, with a record of each step."""
    df = cut(snapshot.df, as_of=as_of)
    record: dict[str, Any] = {"snapshot": snapshot.id, "as_of": as_of}
    if convert_to and (snapshot.meta["currency"] or "").upper() != convert_to.upper():
        if fx is None:
            raise MidasError("incompatible_series", f"Converting {snapshot.meta['symbol']} from {snapshot.meta['currency'] or 'no currency'} to {convert_to} needs an exchange-rate snapshot.",
                             "Pass fx=<snapshot id of the rate> (market_fetch it first).")
        df, conv = convert_frame(df, snapshot.meta, fx, convert_to, as_of=as_of)
        record["conversion"] = conv
    if resample:
        df = resample_frame(df, resample, snapshot.meta["frequency"])
        record["resampled"] = resample.upper()
    df = cut(df, start=start, end=end)
    if field not in df.columns:
        raise MidasError("invalid_request", f"Snapshot {snapshot.id} has no '{field}' column (has: {', '.join(df.columns)}).", "Use close, or a listed column.")
    return df[field].dropna(), record


def series_view(snapshot: Snapshot, *, field: str = "close", start: Optional[str] = None, end: Optional[str] = None,
                as_of: Optional[str] = None, returns: Optional[str] = None, vol_window: Optional[int] = None,
                drawdown: bool = False, resample: Optional[str] = None, rebase: bool = False, convert_to: Optional[str] = None,
                fx: Optional[Snapshot] = None, limit: int = 120, cursor: Optional[int] = None) -> dict[str, Any]:
    """Points plus derived columns and a summary, with provenance. Points are paginated (oldest first)."""
    s, record = prepare(snapshot, field=field, start=start, end=end, as_of=as_of, convert_to=convert_to, fx=fx, resample=resample)
    if s.empty:
        raise MidasError("no_data", "The series is empty in that range.", "Widen start/end or move as_of later.")
    ppy = ppy_of(snapshot.meta)
    if resample:
        ppy = {"W": 52.0, "M": 12.0, "Q": 4.0, "A": 1.0}[resample.upper()]
    frame = pd.DataFrame({"value": s})
    warnings: list[str] = []
    unit = (snapshot.meta.get("unit") or "").lower()
    if returns and any(u in unit for u in RATE_UNITS):
        warnings.append(f"'{snapshot.meta['symbol']}' is a rate ({snapshot.meta['unit']}): percent returns of a rate are rarely meaningful; consider the level or its change.")
    if returns in ("simple", "log"):
        frame["return"] = ind.pct_returns(s, returns)
    if vol_window:
        frame[f"vol_{vol_window}"] = ind.rolling_vol(s, int(vol_window), ppy)
    if drawdown:
        frame["drawdown_pct"] = ind.drawdown(s)
    if rebase:
        frame["rebased"] = s / s.iloc[0] * 100.0
    total = len(frame)
    limit = max(1, min(int(limit), 1000))
    begin = max(0, total - limit) if cursor is None else max(0, min(int(cursor), total))
    page = frame.iloc[begin: begin + limit]
    points = [{"date": ts.strftime("%Y-%m-%d"), **{k: (None if pd.isna(v) else round(float(v), 8)) for k, v in row.items()}}
              for ts, row in page.iterrows()]
    return {
        "snapshot": provenance(snapshot.meta), "field": field, "processing": record, "summary": simple_stats(s, ppy),
        "columns": list(frame.columns), "total": total, "points": points,
        "next_cursor": begin + limit if begin + limit < total else None, "first_cursor": begin, "warnings": warnings,
        "note": "Points are oldest first; without a cursor the most recent `limit` points are returned.",
    }


def compare(snaps: list[Snapshot], *, field: str = "close", start: Optional[str] = None, end: Optional[str] = None,
            as_of: Optional[str] = None, resample: Optional[str] = None, convert_to: Optional[str] = None,
            fx: Optional[dict[str, Snapshot]] = None, allow_incompatible: bool = False, limit: int = 60) -> dict[str, Any]:
    """Align N series on their common dates; correlation of simple returns and relative performance."""
    if len(snaps) < 2:
        raise MidasError("invalid_request", "Comparing needs at least two snapshots.", "Pass snapshot ids: ['snp_a', 'snp_b'].")
    if len(snaps) > 12:
        raise MidasError("invalid_request", "Compare at most 12 series at once.", "Split the comparison.")
    fx = fx or {}
    warnings: list[str] = []
    prepared: dict[str, pd.Series] = {}
    records: dict[str, Any] = {}
    converted_snaps = []
    for snap in snaps:
        rate = fx.get(snap.id) or (next(iter(fx.values())) if len(fx) == 1 else None)
        s, record = prepare(snap, field=field, start=start, end=end, as_of=as_of, convert_to=convert_to, fx=rate, resample=resample)
        label = snap.meta["symbol"] if list(x.meta["symbol"] for x in snaps).count(snap.meta["symbol"]) == 1 else snap.id
        prepared[label] = s
        records[snap.id] = record
        meta = dict(snap.meta)
        if convert_to and record.get("conversion", {}).get("converted"):
            meta["currency"] = convert_to.upper()
        if resample:
            meta["frequency"] = resample.upper()
        converted_snaps.append(Snapshot(meta, snap.df))
    warnings.extend(check_compatible(converted_snaps, allow_incompatible=allow_incompatible))
    frame = pd.DataFrame(prepared).dropna()
    if len(frame) < 2:
        raise MidasError("no_data", "The series share fewer than 2 common dates.", "Check the ranges, or resample to a common frequency (resample='M').")
    rets = frame.pct_change().dropna()
    corr = rets.corr() if len(rets) > 2 else pd.DataFrame(np.nan, index=frame.columns, columns=frame.columns)
    rebased = frame / frame.iloc[0] * 100.0
    ppy = {"W": 52.0, "M": 12.0, "Q": 4.0, "A": 1.0}[resample.upper()] if resample else ppy_of(snaps[0].meta)
    stats = {name: simple_stats(frame[name], ppy) for name in frame.columns}
    step = max(1, math.ceil(len(rebased) / max(1, limit)))
    sampled = rebased.iloc[::step]
    if sampled.index[-1] != rebased.index[-1]:
        sampled = pd.concat([sampled, rebased.iloc[[-1]]])
    final = rebased.iloc[-1]
    best, worst = final.idxmax(), final.idxmin()
    return {
        "series": list(frame.columns), "snapshots": [provenance(s.meta) for s in snaps], "processing": records,
        "common_range": {"start": frame.index[0].strftime("%Y-%m-%d"), "end": frame.index[-1].strftime("%Y-%m-%d"), "rows": int(len(frame))},
        "correlation": {a: {b: (None if pd.isna(corr.loc[a, b]) else round(float(corr.loc[a, b]), 4)) for b in frame.columns} for a in frame.columns},
        "correlation_basis": f"simple returns, {len(rets)} observations",
        "relative_performance": {name: round(float(final[name]), 4) for name in frame.columns},
        "leader": best, "laggard": worst, "stats": stats, "warnings": warnings,
        "points": [{"date": ts.strftime("%Y-%m-%d"), **{k: round(float(v), 4) for k, v in row.items()}} for ts, row in sampled.iterrows()],
        "sampling": {"every": step, "of": int(len(rebased))},
    }
