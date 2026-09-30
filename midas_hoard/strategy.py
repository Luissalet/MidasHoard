"""Declarative strategy specs: validated with precise, fixable messages; never code.

A spec is JSON: a universe of snapshot ids, named indicators, entry/exit rule trees, sizing, rebalancing, costs,
slippage and a benchmark. ``validate_spec`` returns every problem at once, each with a ``path`` and a ``hint`` so an
agent can repair the spec in one round.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Callable, Optional

from .indicators import INDICATOR_DEFS, MAX_WINDOW

FIELDS = ("open", "high", "low", "close", "volume")
COMPARATORS = (">", ">=", "<", "<=", "==", "!=", "crosses_above", "crosses_below")
SIZING = ("all_in", "fixed_fraction", "vol_target")
REBALANCE = ("daily", "weekly", "monthly")
EXECUTION = ("close", "next_open")
TOP_KEYS = {"name", "family", "note", "universe", "indicators", "entry", "exit", "allow_short", "sizing", "rebalance", "costs_bps",
            "slippage_bps", "execution", "benchmark", "initial_capital", "currency", "fx", "rf"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
MAX_DEPTH = 5

EXAMPLE_SPEC = {
    "name": "sma-trend", "universe": ["snp_xxxxxxxxxx"],
    "indicators": {"fast": {"type": "sma", "window": 20}, "slow": {"type": "sma", "window": 50}},
    "entry": {"all": [{"left": "fast", "op": ">", "right": "slow"}]},
    "exit": {"all": [{"left": "fast", "op": "<", "right": "slow"}]},
    "sizing": {"type": "all_in"}, "rebalance": "daily", "costs_bps": 5, "slippage_bps": 2,
}


class Issues:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, path: str, message: str, hint: str) -> None:
        self.items.append({"path": path, "message": message, "hint": hint})


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_indicator(name: str, d: Any, issues: Issues, universe: list[str], snapshot_exists: Callable[[str], bool]) -> Optional[dict[str, Any]]:
    path = f"indicators.{name}"
    if not isinstance(d, dict):
        issues.add(path, "An indicator is an object.", 'Example: {"type": "sma", "window": 20}.')
        return None
    typ = d.get("type")
    if typ not in INDICATOR_DEFS:
        issues.add(f"{path}.type", f"Unknown indicator type {typ!r}.", f"Use one of: {', '.join(sorted(INDICATOR_DEFS))}.")
        return None
    spec = INDICATOR_DEFS[typ]["params"]
    out: dict[str, Any] = {"type": typ}
    allowed = {"type", "field", "source", *spec}
    for key in d:
        if key not in allowed:
            issues.add(f"{path}.{key}", f"'{key}' is not a parameter of {typ}.", f"{typ} takes: {', '.join(sorted(allowed - {'type'}))}.")
    field = d.get("field", "close")
    if field not in FIELDS:
        issues.add(f"{path}.field", f"Unknown field {field!r}.", f"Use one of: {', '.join(FIELDS)}.")
        field = "close"
    out["field"] = field
    source = d.get("source", "$asset")
    if source != "$asset":
        if not isinstance(source, str) or source not in universe:
            issues.add(f"{path}.source", f"Unknown source {source!r}.",
                       "Use '$asset' (the asset being traded, default) or the id of another snapshot that is also listed in the universe.")
    out["source"] = source
    for pname, p in spec.items():
        val = d.get(pname, p["default"])
        ppath = f"{path}.{pname}"
        if val is None:
            issues.add(ppath, f"'{pname}' is required for {typ}.", f"Add \"{pname}\": <number>.")
            continue
        if p["type"] == "int":
            if not _num(val) or val != int(val):
                issues.add(ppath, f"'{pname}' must be a whole number, got {val!r}.", f"Use an integer between {int(p['low'])} and {int(p['high'])}.")
                continue
            val = int(val)
        elif p["type"] == "float":
            if not _num(val):
                issues.add(ppath, f"'{pname}' must be a number, got {val!r}.", f"Use a number between {p['low']} and {p['high']}.")
                continue
            val = float(val)
        else:
            if p["choices"] and val not in p["choices"]:
                issues.add(ppath, f"'{pname}' must be one of {list(p['choices'])}, got {val!r}.", f"Use one of: {', '.join(p['choices'])}.")
                continue
        if p["type"] in ("int", "float") and not (p["low"] <= val <= p["high"]):
            issues.add(ppath, f"'{pname}' {val} is outside [{p['low']}, {p['high']}].", f"Use a value between {p['low']} and {p['high']} (max window {MAX_WINDOW}).")
            continue
        out[pname] = val
    return out


def _operand(value: Any, path: str, names: set[str], issues: Issues) -> Any:
    if _num(value):
        return float(value)
    if isinstance(value, str):
        if value in names or value in FIELDS:
            return value
        issues.add(path, f"Unknown operand {value!r}.", f"Use an indicator name ({', '.join(sorted(names)) or 'define one in indicators'}), a field ({', '.join(FIELDS)}) or a number.")
        return None
    issues.add(path, f"An operand is a name or a number, got {value!r}.", 'Example: {"left": "fast", "op": ">", "right": "slow"} or "right": 30.')
    return None


def _rule(node: Any, path: str, names: set[str], issues: Issues, depth: int = 0) -> Optional[dict[str, Any]]:
    if depth > MAX_DEPTH:
        issues.add(path, "Rule nested too deeply.", f"Keep all/any/not nesting under {MAX_DEPTH} levels.")
        return None
    if not isinstance(node, dict):
        issues.add(path, "A rule is an object.", 'Use {"all": [...]}, {"any": [...]}, {"not": {...}} or a comparison {"left","op","right"}.')
        return None
    keys = set(node)
    if keys & {"all", "any"}:
        key = "all" if "all" in node else "any"
        if keys - {key}:
            issues.add(path, f"'{key}' cannot be mixed with other keys ({', '.join(sorted(keys - {key}))}).", "Nest separate objects inside the list.")
        items = node[key]
        if not isinstance(items, list) or not items:
            issues.add(f"{path}.{key}", f"'{key}' needs a non-empty list of rules.", 'Example: {"all": [{"left": "rsi", "op": "<", "right": 30}]}.')
            return None
        out = [_rule(x, f"{path}.{key}[{i}]", names, issues, depth + 1) for i, x in enumerate(items)]
        return {key: out} if all(o is not None for o in out) else None
    if "not" in node:
        inner = _rule(node["not"], f"{path}.not", names, issues, depth + 1)
        return {"not": inner} if inner is not None else None
    missing = [k for k in ("left", "op", "right") if k not in node]
    if missing:
        issues.add(path, f"A comparison needs left, op and right (missing: {', '.join(missing)}).", 'Example: {"left": "close", "op": ">", "right": "slow"}.')
        return None
    extra = keys - {"left", "op", "right"}
    if extra:
        issues.add(path, f"Unknown key(s) in comparison: {', '.join(sorted(extra))}.", "A comparison has only left, op and right.")
    if node["op"] not in COMPARATORS:
        issues.add(f"{path}.op", f"Unknown operator {node['op']!r}.", f"Use one of: {', '.join(COMPARATORS)}.")
        return None
    left = _operand(node["left"], f"{path}.left", names, issues)
    right = _operand(node["right"], f"{path}.right", names, issues)
    if left is None or right is None:
        return None
    if _num(left) and _num(right):
        issues.add(path, "Both sides are constants: the comparison never changes.", "Compare an indicator or a price field with something.")
    return {"left": left, "op": node["op"], "right": right}


def validate_spec(spec: Any, *, snapshot_exists: Callable[[str], bool] = lambda s: True) -> dict[str, Any]:
    """Return ``{ok, issues[], normalized?, spec_hash?, summary?}``. ``issues`` items have path, message, hint."""
    issues = Issues()
    if not isinstance(spec, dict):
        issues.add("", "The spec must be a JSON object.", "See the example in the tool description.")
        return {"ok": False, "issues": issues.items, "example": EXAMPLE_SPEC}
    for key in spec:
        if key not in TOP_KEYS:
            issues.add(key, f"Unknown key '{key}'.", f"Allowed keys: {', '.join(sorted(TOP_KEYS))}.")
    out: dict[str, Any] = {}
    name = spec.get("name", "strategy")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 80):
        issues.add("name", "name must be a string of 1-80 characters.", "Example: \"sma-trend\".")
        name = "strategy"
    out["name"] = name.strip()
    family = spec.get("family") or out["name"]
    out["family"] = str(family)[:80]
    out["note"] = str(spec.get("note", ""))[:500]

    universe = spec.get("universe")
    if not isinstance(universe, list) or not universe or not all(isinstance(u, str) for u in universe):
        issues.add("universe", "universe must be a non-empty list of snapshot ids.", 'Example: ["snp_1a2b3c4d5e"]. Fetch data with market_fetch first.')
        universe = []
    else:
        if len(universe) > 12:
            issues.add("universe", "At most 12 assets.", "Trim the universe.")
        if len(set(universe)) != len(universe):
            issues.add("universe", "Duplicate snapshot ids in the universe.", "List each asset once.")
        for i, sid in enumerate(universe):
            if not snapshot_exists(sid):
                issues.add(f"universe[{i}]", f"Unknown snapshot '{sid}'.", "List snapshots with snapshots_list or fetch with market_fetch.")
    out["universe"] = list(universe)

    indicators = spec.get("indicators", {})
    names: set[str] = set()
    norm_ind: dict[str, Any] = {}
    if not isinstance(indicators, dict):
        issues.add("indicators", "indicators must be an object mapping a name to a definition.", 'Example: {"fast": {"type": "sma", "window": 20}}.')
    else:
        if len(indicators) > 20:
            issues.add("indicators", "At most 20 indicators.", "Drop the ones the rules do not use.")
        for key in indicators:
            if not isinstance(key, str) or not NAME_RE.match(key) or key in FIELDS:
                issues.add(f"indicators.{key}", f"Invalid indicator name {key!r}.",
                           "Use lowercase letters, digits and underscores, starting with a letter, and not a field name (open/high/low/close/volume).")
                continue
            names.add(key)
        for key, definition in indicators.items():
            if key in names:
                res = _validate_indicator(key, definition, issues, universe, snapshot_exists)
                if res is not None:
                    norm_ind[key] = res
    out["indicators"] = norm_ind

    if "entry" not in spec:
        issues.add("entry", "entry is required.", 'Example: {"all": [{"left": "fast", "op": ">", "right": "slow"}]}.')
    else:
        entry = _rule(spec["entry"], "entry", names, issues)
        if entry is not None:
            out["entry"] = entry
    if spec.get("exit") is not None:
        exit_rule = _rule(spec["exit"], "exit", names, issues)
        if exit_rule is not None:
            out["exit"] = exit_rule
    out["allow_short"] = bool(spec.get("allow_short", False))

    sizing = spec.get("sizing", {"type": "all_in"})
    if not isinstance(sizing, dict) or sizing.get("type") not in SIZING:
        issues.add("sizing", f"sizing.type must be one of {list(SIZING)}.", 'Example: {"type": "fixed_fraction", "fraction": 0.5}.')
    else:
        typ = sizing["type"]
        norm: dict[str, Any] = {"type": typ}
        if typ == "fixed_fraction":
            fr = sizing.get("fraction")
            if not _num(fr) or not (0 < fr <= 1):
                issues.add("sizing.fraction", f"fraction must be a number in (0, 1], got {fr!r}.", "Use e.g. 0.5 to hold half of the capital.")
            else:
                norm["fraction"] = float(fr)
        if typ == "vol_target":
            tv, win, lev = sizing.get("target_vol"), sizing.get("window", 20), sizing.get("max_leverage", 1.0)
            if not _num(tv) or not (0.005 <= tv <= 2.0):
                issues.add("sizing.target_vol", f"target_vol must be annual volatility as a fraction (0.005-2.0), got {tv!r}.", "0.10 means 10% a year.")
            else:
                norm["target_vol"] = float(tv)
            if not _num(win) or win != int(win) or not (2 <= win <= MAX_WINDOW):
                issues.add("sizing.window", f"window must be a whole number of bars (2-{MAX_WINDOW}), got {win!r}.", "Use e.g. 20.")
            else:
                norm["window"] = int(win)
            if not _num(lev) or not (0 < lev <= 5):
                issues.add("sizing.max_leverage", f"max_leverage must be in (0, 5], got {lev!r}.", "Use 1.0 for no leverage.")
            else:
                norm["max_leverage"] = float(lev)
        for key in sizing:
            if key not in {"type", "fraction", "target_vol", "window", "max_leverage"}:
                issues.add(f"sizing.{key}", f"Unknown sizing key '{key}'.", "Keys: type, fraction (fixed_fraction), target_vol/window/max_leverage (vol_target).")
        out["sizing"] = norm
    out["rebalance"] = spec.get("rebalance", "daily")
    if out["rebalance"] not in REBALANCE:
        issues.add("rebalance", f"rebalance must be one of {list(REBALANCE)}.", "Targets are re-read on the last bar of each period.")
    for key, default in (("costs_bps", 5.0), ("slippage_bps", 2.0)):
        val = spec.get(key, default)
        if not _num(val) or not (0 <= val <= 1000):
            issues.add(key, f"{key} must be a number between 0 and 1000 (basis points per unit traded), got {val!r}.", "Use 5 for 0.05% per trade.")
        else:
            out[key] = float(val)
    out["execution"] = spec.get("execution", "close")
    if out["execution"] not in EXECUTION:
        issues.add("execution", f"execution must be one of {list(EXECUTION)}.",
                   "'close': a signal on bar t is filled at the close of t and earns from bar t+1. 'next_open': filled at the open of t+1 (needs an open column).")
    cap = spec.get("initial_capital", 10000)
    if not _num(cap) or cap <= 0:
        issues.add("initial_capital", "initial_capital must be a positive number.", "Use 10000.")
    else:
        out["initial_capital"] = float(cap)
    bench = spec.get("benchmark")
    if bench is not None:
        if not isinstance(bench, dict) or not isinstance(bench.get("snapshot"), str) or not snapshot_exists(bench["snapshot"]):
            issues.add("benchmark", "benchmark must be {\"snapshot\": <existing snapshot id>}.", "Default (omit it): buy & hold of the first asset of the universe.")
        else:
            out["benchmark"] = {"snapshot": bench["snapshot"]}
    rf = spec.get("rf")
    if rf is not None:
        if not isinstance(rf, dict) or not isinstance(rf.get("snapshot"), str) or not snapshot_exists(rf["snapshot"]):
            issues.add("rf", "rf must be {\"snapshot\": <snapshot id of an annual rate in percent>}.", "Omit it for a risk-free rate of 0 (stated in the results).")
        else:
            out["rf"] = {"snapshot": rf["snapshot"]}
    if spec.get("currency") is not None:
        if not isinstance(spec["currency"], str) or len(spec["currency"]) != 3:
            issues.add("currency", "currency must be a 3-letter code such as EUR.", "Set it together with fx when the universe mixes currencies.")
        else:
            out["currency"] = spec["currency"].upper()
    fx = spec.get("fx")
    if fx is not None:
        if not isinstance(fx, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in fx.items()):
            issues.add("fx", "fx maps an asset snapshot id to an exchange-rate snapshot id.", 'Example: {"snp_aapl": "snp_eurusd"}.')
        else:
            for k, v in fx.items():
                if not snapshot_exists(v):
                    issues.add(f"fx.{k}", f"Unknown exchange-rate snapshot '{v}'.", "Fetch the pair first (market_fetch).")
            out["fx"] = dict(fx)
    if "fx" in out and "currency" not in out:
        issues.add("currency", "fx needs a target currency.", 'Add "currency": "EUR".')

    if issues.items:
        return {"ok": False, "issues": issues.items, "example": EXAMPLE_SPEC}
    return {"ok": True, "issues": [], "normalized": out, "spec_hash": spec_hash(out), "summary": summarize(out)}


def spec_hash(normalized: dict[str, Any]) -> str:
    """Identity of a *variant*: the spec without its labels (name, family, note)."""
    core = {k: v for k, v in normalized.items() if k not in ("name", "family", "note")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def summarize(spec: dict[str, Any]) -> dict[str, Any]:
    return {"assets": len(spec["universe"]), "indicators": sorted(spec["indicators"]), "sizing": spec["sizing"]["type"],
            "rebalance": spec["rebalance"], "costs_bps": spec["costs_bps"], "slippage_bps": spec["slippage_bps"],
            "execution": spec["execution"], "long_short": spec["allow_short"]}


def set_path(spec: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy of ``spec`` with ``a.b.c`` set to value (used by parameter grids)."""
    out = copy.deepcopy(spec)
    node: Any = out
    parts = path.split(".")
    for p in parts[:-1]:
        if not isinstance(node, dict) or p not in node:
            raise KeyError(path)
        node = node[p]
    if not isinstance(node, dict) or parts[-1] not in node:
        raise KeyError(path)
    node[parts[-1]] = value
    return out
