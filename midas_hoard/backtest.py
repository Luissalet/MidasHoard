"""Vectorised backtester (pandas/numpy). Long-only / long-flat by default, optional long/short.

Timing contract (the no-look-ahead rule, covered by tests): a signal computed from bars up to and including bar ``t``
only changes the position that *earns bar t+1's return*. With ``execution = "close"`` the trade is filled at the close
of ``t``; with ``"next_open"`` at the open of ``t+1`` (the overnight gap is earned by the old position, the session by
the new one). Targets are re-read only on rebalance bars. Costs and slippage are charged in basis points on every
change of target weight, on the bar where the new weight starts to earn.

Not modelled (stated in every report): financing of leverage and shorts, borrow fees, taxes, partial fills, market
impact, dividends beyond what the snapshot's prices contain, cash interest on idle capital.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .errors import MidasError
from .series import check_compatible, convert_frame
from .snapshots import Snapshot


# ------------------------------------------------------------------------------------------ preparation
@dataclass
class Universe:
    index: pd.DatetimeIndex
    ids: list[str]
    labels: list[str]
    frames: dict[str, pd.DataFrame]
    ppy: float
    meta: dict[str, dict[str, Any]]
    conversions: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def prepare_universe(spec: dict[str, Any], load: Callable[[str], Snapshot], as_of: Optional[str]) -> Universe:
    """Load the universe cut at ``as_of``, convert currencies explicitly, refuse incompatible mixes, align on common dates."""
    frames: dict[str, pd.DataFrame] = {}
    metas: dict[str, dict[str, Any]] = {}
    conversions: dict[str, Any] = {}
    snaps: list[Snapshot] = []
    for sid in spec["universe"]:
        snap = load(sid)
        df = snap.df if not as_of else snap.df[snap.df.index <= pd.Timestamp(as_of)]
        meta = dict(snap.meta)
        target = spec.get("currency")
        fx_id = (spec.get("fx") or {}).get(sid)
        if target and fx_id and (meta["currency"] or "").upper() != target:
            fx = load(fx_id)
            df, record = convert_frame(df, meta, fx, target, as_of=as_of)
            conversions[sid] = record
            meta["currency"] = target
        frames[sid] = df
        metas[sid] = meta
        snaps.append(Snapshot(meta, df))
    warnings = check_compatible(snaps)
    index = None
    for sid in spec["universe"]:
        idx = frames[sid].index
        index = idx if index is None else index.intersection(idx)
    index = index.sort_values()
    if len(index) < 20:
        raise MidasError("no_data", f"The universe shares only {len(index)} common bars" + (f" up to {as_of}" if as_of else "") + ".",
                         "Fetch longer ranges, or drop assets with short history.")
    for sid in spec["universe"]:
        frames[sid] = frames[sid].reindex(index)
    first = metas[spec["universe"][0]]
    return Universe(index=index, ids=list(spec["universe"]), labels=[metas[s]["symbol"] for s in spec["universe"]], frames=frames,
                    ppy=float(first.get("periods_per_year") or 252.0), meta=metas, conversions=conversions, warnings=warnings)


# ------------------------------------------------------------------------------------------ signals
def _operand(value: Any, asset_frame: pd.DataFrame, computed: dict[str, pd.Series]) -> Any:
    if isinstance(value, (int, float)):
        return float(value)
    if value in computed:
        return computed[value]
    return asset_frame[value]


def _leaf(node: dict[str, Any], asset_frame: pd.DataFrame, computed: dict[str, pd.Series]) -> tuple[pd.Series, pd.Series]:
    left, right = _operand(node["left"], asset_frame, computed), _operand(node["right"], asset_frame, computed)
    idx = asset_frame.index
    a = left if isinstance(left, pd.Series) else pd.Series(left, index=idx)
    b = right if isinstance(right, pd.Series) else pd.Series(right, index=idx)
    ok = a.notna() & b.notna()
    op = node["op"]
    if op == "crosses_above":
        val = (a > b) & (a.shift(1) <= b.shift(1))
        ok = ok & a.shift(1).notna() & b.shift(1).notna()
    elif op == "crosses_below":
        val = (a < b) & (a.shift(1) >= b.shift(1))
        ok = ok & a.shift(1).notna() & b.shift(1).notna()
    else:
        val = {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b, "==": a == b, "!=": a != b}[op]
    return val & ok, ok


def eval_rule(node: dict[str, Any], asset_frame: pd.DataFrame, computed: dict[str, pd.Series]) -> tuple[pd.Series, pd.Series]:
    """(value, known) for a rule tree; an unknown input makes a leaf False and 'not known'."""
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        parts = [eval_rule(n, asset_frame, computed) for n in node[key]]
        val, ok = parts[0]
        for v2, o2 in parts[1:]:
            if key == "all":
                new_val, new_ok = val & v2, (ok & o2) | (ok & ~val) | (o2 & ~v2)
            else:
                new_val, new_ok = val | v2, (ok & o2) | (ok & val) | (o2 & v2)
            val, ok = new_val & new_ok, new_ok
        return val, ok
    if "not" in node:
        v, o = eval_rule(node["not"], asset_frame, computed)
        return ~v & o, o
    return _leaf(node, asset_frame, computed)


def compute_indicators(spec: dict[str, Any], uni: Universe, sid: str) -> dict[str, pd.Series]:
    frame = uni.frames[sid]
    out: dict[str, pd.Series] = {}
    for name, d in spec["indicators"].items():
        src = frame if d["source"] == "$asset" else None
        if src is None:
            raise_if_missing = d["source"] not in uni.frames
            if raise_if_missing:
                raise MidasError("invalid_spec", f"Indicator '{name}' uses source {d['source']}, which is not in the universe.",
                                 "Cross-asset signals must come from a snapshot that is also listed in the universe.")
            src = uni.frames[d["source"]]
        series = src[d["field"]] if d["field"] in src.columns else None
        if series is None:
            raise MidasError("invalid_spec", f"Indicator '{name}' needs the '{d['field']}' column, which snapshot {sid} lacks.",
                             "Use field 'close' for series without OHLC.")
        params = {k: v for k, v in d.items() if k not in ("type", "field", "source")}
        out[name] = INDICATOR_FN(d["type"], series, params, uni.ppy)
    return out


def INDICATOR_FN(kind: str, series: pd.Series, params: dict[str, Any], ppy: float) -> pd.Series:
    return ind.INDICATOR_DEFS[kind]["fn"](series, params, ppy)


def signal_for_asset(spec: dict[str, Any], uni: Universe, sid: str) -> pd.Series:
    """The raw state per bar in {-1, 0, +1}: entry -> +1, exit -> 0 (or -1 when shorting), both at once -> flat."""
    frame = uni.frames[sid]
    computed = compute_indicators(spec, uni, sid)
    entry_val, entry_ok = eval_rule(spec["entry"], frame, computed)
    sig = pd.Series(np.nan, index=frame.index)
    exit_value = -1.0 if spec["allow_short"] else 0.0
    if "exit" in spec:
        exit_val, _ = eval_rule(spec["exit"], frame, computed)
    else:
        exit_val = entry_ok & ~entry_val
    sig[entry_val] = 1.0
    sig[exit_val] = exit_value
    sig[entry_val & exit_val] = 0.0
    return sig.ffill().fillna(0.0)


def rebalance_mask(index: pd.DatetimeIndex, rule: str) -> np.ndarray:
    if rule == "daily":
        return np.ones(len(index), dtype=bool)
    period = index.to_period("W-FRI" if rule == "weekly" else "M")
    last = pd.Series(index, index=index).groupby(period).transform("max")
    return (last.to_numpy() == index.to_numpy())


def target_weights(spec: dict[str, Any], uni: Universe) -> pd.DataFrame:
    """Target weight per bar and asset (before the one-bar lag and the rebalance schedule)."""
    k = len(uni.ids)
    cols = {}
    sizing = spec["sizing"]
    for sid in uni.ids:
        sig = signal_for_asset(spec, uni, sid)
        if sizing["type"] == "all_in":
            size = pd.Series(1.0, index=uni.index)
        elif sizing["type"] == "fixed_fraction":
            size = pd.Series(sizing["fraction"], index=uni.index)
        else:
            close = uni.frames[sid]["close"]
            vol = close.pct_change().rolling(sizing["window"], min_periods=sizing["window"]).std() * math.sqrt(uni.ppy)
            size = (sizing["target_vol"] / vol.replace(0.0, np.nan)).clip(lower=0.0, upper=sizing["max_leverage"]).fillna(0.0)
        cols[sid] = sig * size / k
    return pd.DataFrame(cols, index=uni.index)


# ------------------------------------------------------------------------------------------ simulation
@dataclass
class Simulation:
    pos: pd.DataFrame       # weight held during each bar
    asset_ret: pd.DataFrame  # close-to-close simple returns
    gross: pd.Series
    cost: pd.Series
    net: pd.Series
    turnover: pd.Series
    fills: dict[str, pd.Series]  # per-asset fill price of the trade made at the start of each bar


def simulate(spec: dict[str, Any], uni: Universe) -> Simulation:
    targets = target_weights(spec, uni)
    mask = rebalance_mask(uni.index, spec["rebalance"])
    w_eff = targets.where(pd.Series(mask, index=uni.index), other=np.nan).ffill().fillna(0.0)
    pos = w_eff.shift(1).fillna(0.0)  # a signal on bar t acts on bar t+1
    closes = pd.DataFrame({s: uni.frames[s]["close"] for s in uni.ids})
    asset_ret = closes.pct_change().fillna(0.0)
    prev = pos.shift(1).fillna(0.0)
    if spec["execution"] == "next_open":
        for s in uni.ids:
            if "open" not in uni.frames[s].columns or uni.frames[s]["open"].isna().any():
                raise MidasError("invalid_spec", f"execution 'next_open' needs an 'open' column with no gaps, which {uni.meta[s]['symbol']} lacks.",
                                 "Use execution 'close' for series without OHLC.")
        opens = pd.DataFrame({s: uni.frames[s]["open"] for s in uni.ids})
        gap = (opens / closes.shift(1) - 1.0).fillna(0.0)
        intraday = (closes / opens - 1.0).fillna(0.0)
        gross = (prev * gap + pos * intraday).sum(axis=1)
        fills = {s: opens[s] for s in uni.ids}
    else:
        gross = (pos * asset_ret).sum(axis=1)
        fills = {s: closes[s].shift(1) for s in uni.ids}
    turnover = (pos - prev).abs().sum(axis=1)
    cost = turnover * (spec["costs_bps"] + spec["slippage_bps"]) / 1e4
    return Simulation(pos=pos, asset_ret=asset_ret, gross=gross, cost=cost, net=gross - cost, turnover=turnover, fills=fills)


def extract_trades(sim: Simulation, uni: Universe, spec: dict[str, Any], eval_start: Optional[pd.Timestamp]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    round_trip = 2.0 * (spec["costs_bps"] + spec["slippage_bps"]) / 1e4
    idx = uni.index
    for sid, label in zip(uni.ids, uni.labels):
        sign = np.sign(sim.pos[sid].to_numpy())
        weight = np.abs(sim.pos[sid].to_numpy())
        fill = sim.fills[sid].to_numpy()
        closes = uni.frames[sid]["close"].to_numpy()
        side, start = 0.0, 0
        for t in range(len(idx)):
            s = sign[t]
            if s != side:
                if side != 0.0 and t >= 1:
                    trades.append(_trade(label, sid, side, start, t, fill, closes, weight, idx, round_trip, spec["execution"], False))
                if s != 0.0:
                    start = t
                side = s
        if side != 0.0:
            trades.append(_trade(label, sid, side, start, len(idx) - 1, fill, closes, weight, idx, round_trip, spec["execution"], True))
    if eval_start is not None:
        trades = [t for t in trades if pd.Timestamp(t["exit_date"]) >= eval_start]
    trades.sort(key=lambda t: (t["entry_date"], t["asset"]))
    return trades


def _trade(label, sid, side, start, end, fill, closes, weight, idx, round_trip, execution, still_open) -> dict[str, Any]:
    entry_price = fill[start] if not np.isnan(fill[start]) else closes[max(0, start - 1)]
    if still_open:
        exit_price, exit_i = closes[end], end
    else:
        exit_price, exit_i = (fill[end] if not np.isnan(fill[end]) else closes[end]), end
    gross = side * (exit_price / entry_price - 1.0) if entry_price else 0.0
    entry_i = start if execution == "next_open" else max(0, start - 1)
    exit_date_i = exit_i if (still_open or execution == "next_open") else max(0, exit_i - 1)
    return {"asset": label, "snapshot": sid, "side": "long" if side > 0 else "short", "entry_date": idx[entry_i].strftime("%Y-%m-%d"),
            "entry_price": float(entry_price), "exit_date": idx[exit_date_i].strftime("%Y-%m-%d"), "exit_price": float(exit_price),
            "weight": float(weight[start:(end + 1 if still_open else max(end, start + 1))].mean()) if end >= start else 0.0, "bars": int(end - start + (1 if still_open else 0)),
            "return": float(gross - round_trip), "gross_return": float(gross), "open": bool(still_open)}


# ------------------------------------------------------------------------------------------ metrics
def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else x


def drawdown_info(equity: pd.Series) -> dict[str, Any]:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    max_dd = float(dd.min()) if len(dd) else 0.0
    # longest stretch below a previous peak
    under = dd < 0
    longest_bars, longest_days, run_start = 0, 0, None
    idx = equity.index
    for i, flag in enumerate(under.to_numpy()):
        if flag and run_start is None:
            run_start = i
        if (not flag or i == len(under) - 1) and run_start is not None:
            end_i = i if flag else i - 1
            bars = end_i - run_start + 1
            days = (idx[end_i] - idx[max(run_start - 1, 0)]).days
            if bars > longest_bars:
                longest_bars, longest_days = bars, days
            run_start = None
    return {"max_drawdown": max_dd, "max_drawdown_duration_bars": int(longest_bars), "max_drawdown_duration_days": int(longest_days), "series": dd}


def rf_per_period(rf_snapshot: Optional[Snapshot], index: pd.DatetimeIndex, ppy: float) -> tuple[pd.Series, dict[str, Any]]:
    if rf_snapshot is None:
        return pd.Series(0.0, index=index), {"source": None, "mean_annual": 0.0, "note": "risk-free rate assumed 0"}
    rate = rf_snapshot.df["close"].dropna()
    aligned = rate.reindex(rate.index.union(index)).ffill().reindex(index).fillna(0.0) / 100.0
    per = (1.0 + aligned) ** (1.0 / ppy) - 1.0
    return per, {"source": rf_snapshot.id, "symbol": rf_snapshot.meta["symbol"], "mean_annual": float(aligned.mean()),
                 "note": f"risk-free from snapshot {rf_snapshot.id} ({rf_snapshot.meta['symbol']}, percent per year, as-of join); idle cash itself earns 0"}


def perf_metrics(net: pd.Series, ppy: float, rf_pp: pd.Series, initial: float = 1.0) -> dict[str, Any]:
    """Return/risk numbers from a net return series (one value per bar)."""
    net = net.astype("float64")
    n = len(net)
    equity = initial * (1.0 + net).cumprod()
    years = (net.index[-1] - net.index[0]).days / 365.25 if n > 1 else 0.0
    total = float(equity.iloc[-1] / initial - 1.0) if n else 0.0
    cagr = float((equity.iloc[-1] / initial) ** (1.0 / years) - 1.0) if years >= 0.25 and equity.iloc[-1] > 0 else None
    std = float(net.std(ddof=1)) if n > 1 else 0.0
    excess = net - rf_pp.reindex(net.index).fillna(0.0)
    ex_std = float(excess.std(ddof=1)) if n > 1 else 0.0
    sharpe_pp = float(excess.mean() / ex_std) if ex_std > 0 else None
    down = np.minimum(excess.to_numpy(), 0.0)
    dd_dev = math.sqrt(float(np.mean(down ** 2))) if n else 0.0
    sortino = float(excess.mean() / dd_dev * math.sqrt(ppy)) if dd_dev > 0 else None
    dd = drawdown_info(pd.concat([pd.Series([initial], index=[net.index[0] - pd.Timedelta(days=1)]), equity]) if n else equity)
    max_dd = dd["max_drawdown"]
    return {
        "bars": n, "total_return": _f(total), "cagr": _f(cagr), "ann_vol": _f(std * math.sqrt(ppy)),
        "sharpe": _f(sharpe_pp * math.sqrt(ppy)) if sharpe_pp is not None else None, "sharpe_per_period": _f(sharpe_pp),
        "sortino": _f(sortino), "max_drawdown": _f(max_dd), "max_drawdown_duration_bars": dd["max_drawdown_duration_bars"],
        "max_drawdown_duration_days": dd["max_drawdown_duration_days"],
        "calmar": _f(cagr / abs(max_dd)) if cagr is not None and max_dd < 0 else None,
        "annualised_reliable": bool(years >= 1.0), "years": _f(years),
        "skew": _f(net.skew()) if n > 2 else None, "kurtosis": _f(net.kurt() + 3.0) if n > 3 else None,
    }, equity, dd["series"]


# ------------------------------------------------------------------------------------------ driver
@dataclass
class BacktestOutput:
    metrics: dict[str, Any]
    benchmark: dict[str, Any]
    equity: pd.DataFrame
    trades: list[dict[str, Any]]
    window: dict[str, Any]
    warnings: list[str]
    conversions: dict[str, Any]
    rf: dict[str, Any]
    net: pd.Series
    sim: Simulation
    universe: Universe


def run_backtest(spec: dict[str, Any], load: Callable[[str], Snapshot], *, as_of: Optional[str] = None, eval_start: Optional[str] = None) -> BacktestOutput:
    """Run a validated, normalised spec. ``as_of`` cuts all data (point-in-time); ``eval_start`` only moves where the
    reported window begins (earlier bars still warm the indicators and positions)."""
    uni = prepare_universe(spec, load, as_of)
    sim = simulate(spec, uni)
    start_ts = pd.Timestamp(eval_start) if eval_start else None
    window_mask = (uni.index >= start_ts) if start_ts is not None else np.ones(len(uni.index), dtype=bool)
    if int(np.sum(window_mask)) < 2:
        raise MidasError("no_data", "The evaluation window has fewer than 2 bars.", "Move eval_start/as_of so the window contains data.")
    net = sim.net[window_mask]
    index = net.index
    rf_snap = load(spec["rf"]["snapshot"]) if "rf" in spec else None
    rf_pp, rf_info = rf_per_period(rf_snap, index, uni.ppy)
    initial = spec["initial_capital"]
    metrics, equity, dd = perf_metrics(net, uni.ppy, rf_pp, initial)
    years = max(metrics["years"] or 0.0, 1e-9)
    exposure = sim.pos[window_mask].abs().sum(axis=1)
    metrics["exposure"] = _f(exposure.mean())
    metrics["turnover_per_year"] = _f(sim.turnover[window_mask].sum() / years) if years > 0.05 else None
    eq_prev = equity.shift(1).fillna(initial)
    metrics["costs_paid"] = _f((sim.cost[window_mask] * eq_prev).sum())
    metrics["costs_pct_of_initial"] = _f(metrics["costs_paid"] / initial)
    trades = extract_trades(sim, uni, spec, start_ts)
    metrics["trades"] = len(trades)
    closed = [t for t in trades if not t["open"]]
    metrics["hit_rate"] = _f(sum(1 for t in trades if t["return"] > 0) / len(trades)) if trades else None
    metrics["closed_trades"] = len(closed)
    metrics["rf"] = rf_info
    # benchmark: buy & hold of the chosen snapshot (default: first asset)
    bench_id = (spec.get("benchmark") or {}).get("snapshot") or spec["universe"][0]
    bsnap = load(bench_id)
    bdf = bsnap.df if not as_of else bsnap.df[bsnap.df.index <= pd.Timestamp(as_of)]
    warn = list(uni.warnings)
    bmeta = dict(bsnap.meta)
    if spec.get("currency") and (bmeta["currency"] or "").upper() not in ("", spec["currency"]):
        fx_id = (spec.get("fx") or {}).get(bench_id)
        if fx_id:
            bdf, rec = convert_frame(bdf, bmeta, load(fx_id), spec["currency"], as_of=as_of)
            bmeta["currency"] = spec["currency"]
        else:
            warn.append(f"benchmark {bmeta['symbol']} is in {bmeta['currency']}, the strategy in {spec['currency']}: no fx given for it, compared unconverted.")
    bclose = bdf["close"].reindex(bdf.index.union(uni.index)).ffill().reindex(uni.index)
    bret = bclose.pct_change().fillna(0.0)[window_mask]
    bench_metrics, bench_equity, _ = perf_metrics(bret, uni.ppy, rf_pp, initial)
    if bmeta["frequency"] != uni.meta[spec["universe"][0]]["frequency"]:
        warn.append("the benchmark has a different frequency from the universe: it is carried forward between its observations.")
    aligned = pd.concat([net, bret], axis=1).dropna()
    beta = corr = None
    if len(aligned) > 3 and aligned.iloc[:, 1].var() > 0:
        beta = _f(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]) / aligned.iloc[:, 1].var())
        with np.errstate(invalid="ignore", divide="ignore"):  # a flat series has no correlation
            corr = _f(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    bench = {"snapshot": bench_id, "symbol": bmeta["symbol"], **{k: bench_metrics[k] for k in ("total_return", "cagr", "ann_vol", "sharpe", "max_drawdown")},
             "excess_total_return": _f((metrics["total_return"] or 0.0) - (bench_metrics["total_return"] or 0.0)), "beta": beta, "correlation": corr}
    eq = pd.DataFrame({"equity": equity, "drawdown": dd.reindex(equity.index), "exposure": exposure, "benchmark_equity": bench_equity})
    window = {"start": index[0].strftime("%Y-%m-%d"), "end": index[-1].strftime("%Y-%m-%d"), "bars": int(len(index)),
              "as_of": as_of, "eval_start": eval_start, "data_start": uni.index[0].strftime("%Y-%m-%d")}
    if not metrics["annualised_reliable"]:
        warn.append(f"the window is {metrics['years']:.2f} years long: annualised figures (CAGR, volatility, Sharpe) are noisy.")
    return BacktestOutput(metrics=metrics, benchmark=bench, equity=eq, trades=trades, window=window, warnings=warn,
                          conversions=uni.conversions, rf=rf_info, net=net, sim=sim, universe=uni)
