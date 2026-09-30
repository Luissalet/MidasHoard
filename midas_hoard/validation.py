"""Honest validation of a backtest: walk-forward, permutation test, bootstrap interval, multiple-testing correction.

Everything is seeded and reports its seed and N, so a figure can be reproduced. Nothing here tunes a strategy: the
only optimisation is the optional walk-forward grid, where each window chooses its parameters on the past alone and
is then judged on the window it never saw; every grid candidate is logged as a variant by the caller.
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestOutput, perf_metrics, run_backtest
from .errors import MidasError
from .snapshots import Snapshot
from .strategy import set_path, validate_spec

ND = NormalDist()
EULER = 0.5772156649015329


def _sharpe_pp(excess: np.ndarray) -> np.ndarray:
    """Per-period Sharpe of each row of a 2-D array (or of a 1-D array)."""
    mean = excess.mean(axis=-1)
    std = excess.std(axis=-1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(std > 0, mean / std, 0.0)


def bootstrap_sharpe(out: BacktestOutput, *, n: int = 1000, block: int = 10, seed: int = 0, level: float = 0.95) -> dict[str, Any]:
    """Circular block bootstrap of the net returns: a confidence interval for the annualised Sharpe ratio."""
    net = out.net.to_numpy(dtype="float64")
    rf = out.universe  # only for ppy
    ppy = rf.ppy
    T = len(net)
    if T < 30:
        raise MidasError("no_data", f"Only {T} bars: too few for a bootstrap.", "Use a longer window.")
    n = int(max(100, min(n, 20000)))
    block = int(max(1, min(block, T // 2)))
    rng = np.random.default_rng(seed)
    nb = math.ceil(T / block)
    starts = rng.integers(0, T, size=(n, nb))
    idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % T).reshape(n, nb * block)[:, :T]
    sharpes = _sharpe_pp(net[idx]) * math.sqrt(ppy)
    lo, hi = np.percentile(sharpes, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    point = float(_sharpe_pp(net) * math.sqrt(ppy))
    return {"method": "circular block bootstrap of net returns", "n": n, "block": block, "seed": seed, "level": level,
            "sharpe": point, "ci_low": float(lo), "ci_high": float(hi), "includes_zero": bool(lo <= 0 <= hi),
            "note": "Risk-free ignored inside the bootstrap (excess returns would shift both ends equally)."}


def permutation_test(out: BacktestOutput, *, n: int = 1000, block: int = 20, seed: int = 0) -> dict[str, Any]:
    """Block-permute the position rows against the real asset returns: same exposure and trade structure, no timing."""
    start = out.net.index[0]
    mask = out.sim.pos.index >= start
    pos = out.sim.pos[mask].to_numpy(dtype="float64")
    ret = out.sim.asset_ret[mask].to_numpy(dtype="float64")
    T = len(pos)
    if T < 60:
        raise MidasError("no_data", f"Only {T} bars: too few for a permutation test.", "Use a longer window.")
    n = int(max(100, min(n, 20000)))
    block = int(max(2, min(block, T // 4)))
    cost_rate = (out.sim.cost[mask].sum() / max(out.sim.turnover[mask].sum(), 1e-12)) if out.sim.turnover[mask].sum() > 0 else 0.0
    ppy = out.universe.ppy
    observed = float(_sharpe_pp(out.net.to_numpy()) * math.sqrt(ppy))
    nb = math.ceil(T / block)
    rng = np.random.default_rng(seed)
    blocks = [np.arange(i * block, min((i + 1) * block, T)) for i in range(nb)]
    stats = np.empty(n)
    for i in range(n):
        order = rng.permutation(nb)
        idx = np.concatenate([blocks[j] for j in order])
        p = pos[idx]
        prev = np.vstack([np.zeros((1, p.shape[1])), p[:-1]])
        net = (p * ret).sum(axis=1) - np.abs(p - prev).sum(axis=1) * cost_rate
        stats[i] = _sharpe_pp(net) * math.sqrt(ppy)
    p_value = (1 + int(np.sum(stats >= observed))) / (n + 1)
    return {"method": "block permutation of positions against actual returns", "statistic": "annualised Sharpe (net of costs)",
            "observed": observed, "p_value": float(p_value), "n": n, "block": block, "seed": seed,
            "null_mean": float(stats.mean()), "null_std": float(stats.std()), "null_p05": float(np.percentile(stats, 5)),
            "null_p95": float(np.percentile(stats, 95)),
            "reading": ("small p: the timing of the positions mattered beyond how much and how long the strategy was invested"
                        if p_value < 0.05 else "not small: the result is what random timing with the same exposure often gives"),
            "note": "One-sided. A buy-and-hold-like strategy on a rising asset will not look special here: that is the point."}


def multiple_testing(sharpe_pp: Optional[float], n_obs: int, trial_sharpes: list[float], n_variants: int, *, skew: Optional[float] = None,
                     kurt: Optional[float] = None, ppy: float = 252.0, family: str = "") -> dict[str, Any]:
    """Bonferroni haircut and (with >= 3 logged trials) the deflated Sharpe ratio, given the variants tried."""
    out: dict[str, Any] = {"family": family, "variants_tried": int(n_variants),
                           "methods": ["Bonferroni: p-value of the Sharpe t-statistic multiplied by the number of variants; haircut Sharpe = "
                                       "Sharpe * z(adjusted p) / z(p)",
                                       "Deflated Sharpe ratio (Bailey & Lopez de Prado): probability the Sharpe exceeds the maximum expected "
                                       "from the logged trials; needs >= 3 variants with different results"]}
    level = "strong" if n_variants >= 20 else "moderate" if n_variants >= 5 else "none"
    out["warning_level"] = level
    if level != "none":
        out["warning"] = (f"{n_variants} variants of '{family}' have been tried. The best of that many random strategies shows a Sharpe well above "
                          "zero by chance: read every metric of this family as optimistic.")
    if sharpe_pp is None or n_obs < 10:
        out["note"] = "Not enough data for a Sharpe test."
        return out
    t = sharpe_pp * math.sqrt(n_obs)
    p = 2.0 * (1.0 - ND.cdf(abs(t)))
    p_adj = min(1.0, p * max(1, n_variants))
    out["sharpe_annualised"] = sharpe_pp * math.sqrt(ppy)
    out["t_stat"] = t
    out["p_value"] = p
    out["p_value_bonferroni"] = p_adj
    if p <= 0:
        z_raw = 10.0
    else:
        z_raw = ND.inv_cdf(1.0 - p / 2.0)
    if p_adj >= 1.0:
        out["haircut_sharpe_annualised"] = 0.0
    else:
        z_adj = ND.inv_cdf(1.0 - p_adj / 2.0)
        out["haircut_sharpe_annualised"] = float(math.copysign(abs(sharpe_pp) * (z_adj / z_raw), sharpe_pp) * math.sqrt(ppy)) if z_raw > 0 else 0.0
    srs = np.array([s for s in trial_sharpes if s is not None and not math.isnan(s)], dtype=float)
    if len(srs) >= 3 and srs.std(ddof=1) > 0:
        var = float(srs.var(ddof=1))
        n_trials = len(srs)
        emax = math.sqrt(var) * ((1 - EULER) * ND.inv_cdf(1 - 1.0 / n_trials) + EULER * ND.inv_cdf(1 - 1.0 / (n_trials * math.e)))
        sk = skew if skew is not None else 0.0
        ku = kurt if kurt is not None else 3.0
        denom = 1.0 - sk * sharpe_pp + (ku - 1.0) / 4.0 * sharpe_pp ** 2
        z = (sharpe_pp - emax) * math.sqrt(max(n_obs - 1, 1)) / math.sqrt(max(denom, 1e-9))
        out["deflated_sharpe"] = {"probability": float(ND.cdf(z)), "expected_max_sharpe_annualised": emax * math.sqrt(ppy), "trials": n_trials,
                                  "trial_sharpe_std_annualised": math.sqrt(var) * math.sqrt(ppy)}
    else:
        out["deflated_sharpe"] = None
    return out


# ------------------------------------------------------------------------------------------ walk-forward
def grid_combos(spec: dict[str, Any], grid: dict[str, list[Any]], max_combos: int = 50) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(params, valid spec) for every combination of the grid; each value is validated as a spec."""
    if not grid:
        return [({}, spec)]
    paths = list(grid)
    sizes = [len(grid[p]) for p in paths]
    if any(s == 0 for s in sizes):
        raise MidasError("invalid_request", "A grid axis has no values.", "Give each path a non-empty list.")
    total = int(np.prod(sizes))
    if total > max_combos:
        raise MidasError("invalid_request", f"The grid has {total} combinations (max {max_combos}).",
                         "Trim the grid: every candidate counts as a variant in the multiple-testing correction.")
    out = []
    for values in itertools.product(*[grid[p] for p in paths]):
        candidate = spec
        for path, value in zip(paths, values):
            try:
                candidate = set_path(candidate, path, value)
            except KeyError as error:
                raise MidasError("invalid_request", f"Grid path '{path}' does not exist in the spec.",
                                 "Use dotted paths of existing keys, e.g. indicators.fast.window.") from error
        check = validate_spec(candidate)
        if not check["ok"]:
            raise MidasError("invalid_spec", f"Grid values {dict(zip(paths, values))} give an invalid spec: {check['issues'][0]['message']}",
                             check["issues"][0]["hint"], issues=check["issues"])
        out.append((dict(zip(paths, values)), check["normalized"]))
    return out


def walk_forward(spec: dict[str, Any], load: Callable[[str], Snapshot], *, as_of: Optional[str], windows: int = 5,
                 grid: Optional[dict[str, list[Any]]] = None, log_variant: Callable[[dict[str, Any], dict[str, Any]], None] = lambda s, m: None,
                 min_train_fraction: float = 0.3) -> dict[str, Any]:
    """Anchored walk-forward over the dev period.

    Without a grid the spec is fixed and the result shows how stable it is across consecutive out-of-sample windows.
    With a grid each window picks the combination with the best in-sample Sharpe on the data before it, then is judged
    on its own window (never seen when choosing).
    """
    base = run_backtest(spec, load, as_of=as_of)
    index = base.universe.index
    T = len(index)
    windows = int(max(2, min(windows, 12)))
    first_test = int(T * min_train_fraction)
    if T - first_test < windows * 20:
        raise MidasError("no_data", f"{T} bars are too few for {windows} walk-forward windows.", "Use fewer windows or a longer history.")
    edges = np.linspace(first_test, T, windows + 1).astype(int)
    combos = grid_combos(spec, grid or {})
    if grid:
        for params, cand in combos:  # log every candidate once with its full-period result (they are variants tried)
            full = run_backtest(cand, load, as_of=as_of)
            log_variant(cand, {"sharpe_pp": full.metrics["sharpe_per_period"], "n_obs": full.metrics["bars"], "params": params,
                               "total_return": full.metrics["total_return"], "sharpe": full.metrics["sharpe"]})
    rows, oos_parts = [], []
    for w in range(windows):
        train_end_i, test_start_i, test_end_i = edges[w] - 1, edges[w], edges[w + 1] - 1
        train_end, test_start, test_end = index[train_end_i], index[test_start_i], index[test_end_i]
        chosen_params, chosen_spec, is_sharpe = {}, spec, None
        if grid:
            best = None
            for params, cand in combos:
                res = run_backtest(cand, load, as_of=train_end.strftime("%Y-%m-%d"))
                sh = res.metrics["sharpe"]
                if sh is not None and (best is None or sh > best[0]):
                    best = (sh, params, cand)
            if best is None:
                best = (None, combos[0][0], combos[0][1])
            is_sharpe, chosen_params, chosen_spec = best
        res = run_backtest(chosen_spec, load, as_of=test_end.strftime("%Y-%m-%d"), eval_start=test_start.strftime("%Y-%m-%d"))
        oos_parts.append(res.net)
        rows.append({"window": w + 1, "train_end": train_end.strftime("%Y-%m-%d"), "test_start": test_start.strftime("%Y-%m-%d"),
                     "test_end": test_end.strftime("%Y-%m-%d"), "bars": res.metrics["bars"], "chosen_params": chosen_params,
                     "in_sample_sharpe": is_sharpe, "oos_total_return": res.metrics["total_return"], "oos_sharpe": res.metrics["sharpe"],
                     "oos_max_drawdown": res.metrics["max_drawdown"], "oos_trades": res.metrics["trades"],
                     "oos_benchmark_return": res.benchmark["total_return"]})
    stitched = pd.concat(oos_parts)
    stitched = stitched[~stitched.index.duplicated(keep="first")]
    m, _, _ = perf_metrics(stitched, base.universe.ppy, pd.Series(0.0, index=stitched.index), spec["initial_capital"])
    positive = sum(1 for r in rows if (r["oos_total_return"] or 0) > 0)
    oos_sh = [r["oos_sharpe"] for r in rows if r["oos_sharpe"] is not None]
    is_sh = [r["in_sample_sharpe"] for r in rows if r["in_sample_sharpe"] is not None]
    return {
        "method": "anchored walk-forward" + (" with parameter grid (best in-sample Sharpe per window)" if grid else " of a fixed spec"),
        "windows": rows, "stitched_oos": {k: m[k] for k in ("bars", "total_return", "cagr", "ann_vol", "sharpe", "max_drawdown")},
        "positive_windows": positive, "of_windows": windows, "median_oos_sharpe": float(np.median(oos_sh)) if oos_sh else None,
        "oos_sharpe_dispersion": float(np.std(oos_sh, ddof=1)) if len(oos_sh) > 1 else None,
        "efficiency": (float(np.mean(oos_sh) / np.mean(is_sh)) if is_sh and oos_sh and np.mean(is_sh) else None),
        "grid": {"paths": list((grid or {}).keys()), "candidates": len(combos)} if grid else None,
        "note": "The first ~30% of bars is the minimum training history. Windows are consecutive and never overlap.",
    }
