"""A small, safe expression language for machine-checkable invalidation rules. No ``eval``, no attribute access.

    close(aapl.us) < 150
    yoy(CPIAUCSL) > 4
    drawdown(^spx) < -20 and vol(^spx, 20) > 30
    sma(x, 50) < sma(x, 200)
    cross_below(sma(spy.us, 50), sma(spy.us, 200))

Grammar: ``or`` < ``and`` < ``not`` < comparison (``< <= > >= == !=``) < ``+ -`` < ``* /`` < unary minus < atom.
An atom is a number, a function call or a bare symbol (a symbol alone means its close). Symbols are words made of
letters, digits and ``_ . ^ / :`` that start with a letter, ``^`` or ``_``; anything else (``brk-b.us``, ``000660.ks``)
goes in double quotes. Window arguments are plain numbers.

Evaluation is point-in-time: every series is sliced at ``as_of`` before any function runs, and a rule is read at
the last bar on or before ``as_of``. A rule that is true *trips*; one whose inputs have no value yet is ``no_data``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .errors import MidasError

MAX_EXPR = 400
MAX_TOKENS = 160
MAX_DEPTH = 14

FIELD_FUNCS = {"close", "open", "high", "low", "volume", "value"}
# name -> (extra numeric arguments: list of (name, default or None=required))
SERIES_FUNCS: dict[str, list[tuple[str, Optional[float]]]] = {
    "sma": [("window", None)], "ema": [("window", None)], "rsi": [("window", 14)], "roc": [("window", None)],
    "ret": [("window", None)], "change": [("window", None)], "lag": [("window", None)], "vol": [("window", 20)],
    "zscore": [("window", None)], "rmax": [("window", None)], "rmin": [("window", None)],
    "bb_upper": [("window", 20), ("k", 2.0)], "bb_lower": [("window", 20), ("k", 2.0)], "bb_mid": [("window", 20)],
    "pctb": [("window", 20), ("k", 2.0)], "drawdown": [], "yoy": [],
}
EXPR_FUNCS = {"abs": 1, "cross_above": 2, "cross_below": 2}
KEYWORDS = {"and", "or", "not"}
FUNCTIONS = sorted({*FIELD_FUNCS, *SERIES_FUNCS, *EXPR_FUNCS})

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<word>[A-Za-z_^][A-Za-z0-9_.^/:]*)
  | (?P<str>"[^"]*")
  | (?P<op><=|>=|==|!=|<|>|\+|-|\*|/)
  | (?P<lp>\()
  | (?P<rp>\))
  | (?P<comma>,)
""", re.VERBOSE)


class RuleSyntaxError(ValueError):
    def __init__(self, message: str, pos: int = 0, hint: str = ""):
        super().__init__(message)
        self.message, self.pos, self.hint = message, pos, hint


@dataclass
class Tok:
    kind: str
    text: str
    pos: int
    end: int


def tokenize(src: str) -> list[Tok]:
    if len(src) > MAX_EXPR:
        raise RuleSyntaxError(f"Expression longer than {MAX_EXPR} characters.", 0, "Split it into several rules.")
    out: list[Tok] = []
    i = 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            raise RuleSyntaxError(f"Unexpected character {src[i]!r}.", i, "Quote symbols that contain other characters: close(\"brk-b.us\").")
        i = m.end()
        kind = m.lastgroup or ""
        if kind == "ws":
            continue
        text = m.group()
        if kind == "word" and text.lower() in KEYWORDS:
            kind, text = "kw", text.lower()
        out.append(Tok(kind, text, m.start(), m.end()))
        if len(out) > MAX_TOKENS:
            raise RuleSyntaxError("Expression too long.", m.start(), "Split it into several rules.")
    return out


# ------------------------------------------------------------------------------------------------ AST
@dataclass
class Node:
    kind: str
    src: str = ""
    value: Any = None
    name: str = ""
    args: list["Node"] = field(default_factory=list)
    params: list[float] = field(default_factory=list)
    op: str = ""


class Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = tokenize(src)
        self.i = 0

    def peek(self) -> Optional[Tok]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> Tok:
        tok = self.peek()
        if tok is None:
            raise RuleSyntaxError("The expression ends unexpectedly.", len(self.src), "Check for a missing operand or ')'.")
        self.i += 1
        return tok

    def accept(self, kind: str, text: Optional[str] = None) -> Optional[Tok]:
        tok = self.peek()
        if tok and tok.kind == kind and (text is None or tok.text == text):
            self.i += 1
            return tok
        return None

    def expect(self, kind: str, what: str) -> Tok:
        tok = self.accept(kind)
        if tok is None:
            at = self.peek()
            raise RuleSyntaxError(f"Expected {what}" + (f" but found {at.text!r}." if at else " but the expression ended."),
                                  at.pos if at else len(self.src))
        return tok

    def span(self, start: int) -> str:
        end = self.toks[self.i - 1].end if self.i > 0 else start
        return self.src[start:end]

    def parse(self) -> Node:
        if not self.toks:
            raise RuleSyntaxError("Empty expression.", 0, "Write a condition such as close(aapl.us) < 150.")
        node = self.or_(0)
        if self.peek() is not None:
            tok = self.peek()
            raise RuleSyntaxError(f"Unexpected {tok.text!r}.", tok.pos, "Combine conditions with 'and' / 'or'.")
        return node

    def or_(self, depth: int) -> Node:
        self._depth(depth)
        start = self.peek().pos if self.peek() else 0
        left = self.and_(depth + 1)
        while self.accept("kw", "or"):
            right = self.and_(depth + 1)
            left = Node("or", self.span(start), args=[left, right])
        return left

    def and_(self, depth: int) -> Node:
        start = self.peek().pos if self.peek() else 0
        left = self.not_(depth + 1)
        while self.accept("kw", "and"):
            right = self.not_(depth + 1)
            left = Node("and", self.span(start), args=[left, right])
        return left

    def not_(self, depth: int) -> Node:
        self._depth(depth)
        start = self.peek().pos if self.peek() else 0
        if self.accept("kw", "not"):
            return Node("not", self.span(start), args=[self.not_(depth + 1)])
        return self.cmp_(depth + 1)

    def cmp_(self, depth: int) -> Node:
        start = self.peek().pos if self.peek() else 0
        left = self.sum_(depth + 1)
        tok = self.peek()
        if tok and tok.kind == "op" and tok.text in ("<", "<=", ">", ">=", "==", "!="):
            self.i += 1
            right = self.sum_(depth + 1)
            return Node("cmp", self.span(start), op=tok.text, args=[left, right])
        return left

    def sum_(self, depth: int) -> Node:
        self._depth(depth)
        start = self.peek().pos if self.peek() else 0
        left = self.term_(depth + 1)
        while True:
            tok = self.peek()
            if tok and tok.kind == "op" and tok.text in ("+", "-"):
                self.i += 1
                left = Node("bin", self.span(start), op=tok.text, args=[left, self.term_(depth + 1)])
            else:
                return left

    def term_(self, depth: int) -> Node:
        start = self.peek().pos if self.peek() else 0
        left = self.unary_(depth + 1)
        while True:
            tok = self.peek()
            if tok and tok.kind == "op" and tok.text in ("*", "/"):
                self.i += 1
                left = Node("bin", self.span(start), op=tok.text, args=[left, self.unary_(depth + 1)])
            else:
                return left

    def unary_(self, depth: int) -> Node:
        self._depth(depth)
        start = self.peek().pos if self.peek() else 0
        if self.accept("op", "-"):
            return Node("neg", self.span(start), args=[self.unary_(depth + 1)])
        return self.atom_(depth + 1)

    def atom_(self, depth: int) -> Node:
        self._depth(depth)
        tok = self.take()
        if tok.kind == "num":
            return Node("num", tok.text, value=float(tok.text))
        if tok.kind == "lp":
            node = self.or_(depth + 1)
            self.expect("rp", "')'")
            return node
        if tok.kind == "str":
            return Node("sym", tok.text, name=tok.text[1:-1])
        if tok.kind == "word":
            if self.peek() and self.peek().kind == "lp":
                return self.call_(tok, depth)
            return Node("sym", tok.text, name=tok.text)
        raise RuleSyntaxError(f"Unexpected {tok.text!r}.", tok.pos, "An operand is a number, a function call or a symbol.")

    def call_(self, name_tok: Tok, depth: int) -> Node:
        name = name_tok.text.lower()
        if name not in FIELD_FUNCS and name not in SERIES_FUNCS and name not in EXPR_FUNCS:
            raise RuleSyntaxError(f"Unknown function '{name_tok.text}'.", name_tok.pos, f"Functions: {', '.join(FUNCTIONS)}.")
        self.expect("lp", "'('")
        args: list[Node] = []
        params: list[float] = []
        if name in EXPR_FUNCS:
            wanted = EXPR_FUNCS[name]
            for n in range(wanted):
                if n:
                    self.expect("comma", "','")
                args.append(self.or_(depth + 1))
        else:
            args.append(self.or_(depth + 1))
            while self.accept("comma"):
                sign = -1.0 if self.accept("op", "-") else 1.0
                num = self.accept("num")
                if num is None:
                    at = self.peek()
                    raise RuleSyntaxError(f"'{name}' takes plain numbers after the first argument.", at.pos if at else len(self.src),
                                          "Example: sma(x, 50).")
                params.append(sign * float(num.text))
            spec = [] if name in FIELD_FUNCS else SERIES_FUNCS[name]
            if len(params) > len(spec):
                raise RuleSyntaxError(f"'{name}' takes at most {len(spec)} numeric argument(s) after the series.", name_tok.pos)
            for idx in range(len(params), len(spec)):
                pname, default = spec[idx]
                if default is None:
                    raise RuleSyntaxError(f"'{name}' needs a '{pname}' argument.", name_tok.pos, f"Example: {name}(x, 50).")
                params.append(default)
            for (pname, _), val in zip(spec, params):
                if pname == "window":
                    if val != int(val) or not (1 <= val <= ind.MAX_WINDOW):
                        raise RuleSyntaxError(f"'{name}' window must be a whole number between 1 and {ind.MAX_WINDOW}.", name_tok.pos)
                elif pname == "k" and not (0 < val <= 10):
                    raise RuleSyntaxError("Bollinger k must be between 0 and 10.", name_tok.pos)
        self.expect("rp", "')'")
        return Node("call", self.span(name_tok.pos), name=name, args=args, params=params)

    @staticmethod
    def _depth(depth: int) -> None:
        if depth > MAX_DEPTH * 4:
            raise RuleSyntaxError("Expression nested too deeply.", 0)


def parse(src: str) -> Node:
    return Parser(src).parse()


def _walk(node: Node):
    yield node
    for child in node.args:
        yield from _walk(child)


def symbols_of(node: Node) -> list[str]:
    seen: list[str] = []
    for n in _walk(node):
        if n.kind == "sym" and n.name not in seen:
            seen.append(n.name)
    return seen


def validate(src: str) -> dict[str, Any]:
    """Parse only: symbols and functions used, or the precise syntax problem with a hint."""
    try:
        node = parse(src)
    except RuleSyntaxError as error:
        return {"ok": False, "expr": src, "issues": [{"message": error.message, "position": error.pos, "hint": error.hint}]}
    return {"ok": True, "expr": src, "symbols": symbols_of(node),
            "functions": sorted({n.name for n in _walk(node) if n.kind == "call"}), "issues": []}


# ------------------------------------------------------------------------------------------ evaluation
@dataclass
class V:
    """A value: a scalar (``x``) or a series (``s``); ``ppy`` is the periods per year of its source."""

    s: Optional[pd.Series] = None
    x: Optional[float] = None
    ppy: float = 252.0

    @property
    def is_series(self) -> bool:
        return self.s is not None

    def last(self) -> Optional[float]:
        if self.s is not None:
            if self.s.empty:
                return None
            v = self.s.iloc[-1]
            return None if pd.isna(v) else float(v)
        return self.x


@dataclass
class B:
    """A boolean series with a validity mask (False where an input had no value yet)."""

    val: pd.Series
    ok: pd.Series


def _align(a: V, b: V) -> tuple[pd.Series, pd.Series]:
    if a.s is not None and b.s is not None:
        idx = a.s.index.union(b.s.index)
        return a.s.reindex(idx).ffill(), b.s.reindex(idx).ffill()
    if a.s is not None:
        return a.s, pd.Series(float(b.x), index=a.s.index)
    if b.s is not None:
        return pd.Series(float(a.x), index=b.s.index), b.s
    raise AssertionError("both scalar")


class Evaluator:
    """Evaluates parsed rules against snapshots supplied by ``resolve(symbol) -> Snapshot`` (already cut at as_of)."""

    def __init__(self, resolve: Callable[[str], Any]):
        self.resolve = resolve
        self._cache: dict[str, Any] = {}
        self.used: dict[str, str] = {}
        self.terms: list[dict[str, Any]] = []
        self.data_dates: dict[str, str] = {}

    def _snap(self, sym: str):
        if sym not in self._cache:
            snap = self.resolve(sym)
            self._cache[sym] = snap
            self.used[sym] = snap.id
            if len(snap.df):
                self.data_dates[sym] = snap.df.index[-1].strftime("%Y-%m-%d")
        return self._cache[sym]

    # -- series values
    def _series_arg(self, node: Node, field_name: str = "close") -> V:
        if node.kind == "sym":
            snap = self._snap(node.name)
            col = "close" if field_name == "value" else field_name
            if col not in snap.df.columns:
                raise MidasError("bad_rule", f"'{node.name}' has no '{col}' column in snapshot {snap.id}.",
                                 f"Available: {', '.join(snap.df.columns)}.", position=None)
            return V(s=snap.df[col].dropna(), ppy=float(snap.meta.get("periods_per_year") or 252.0))
        return self.num(node)

    def num(self, node: Node) -> V:
        k = node.kind
        if k == "num":
            return V(x=float(node.value))
        if k == "sym":
            return self._series_arg(node)
        if k == "neg":
            v = self.num(node.args[0])
            return V(s=-v.s, ppy=v.ppy) if v.is_series else V(x=-v.x)
        if k == "bin":
            return self._binary(node)
        if k == "call":
            return self._call(node)
        raise MidasError("bad_rule", f"'{node.src}' is a condition, not a number.", "Put a comparison such as '< 150' after it.")

    def _binary(self, node: Node) -> V:
        a, b = self.num(node.args[0]), self.num(node.args[1])
        op = node.op
        if not a.is_series and not b.is_series:
            try:
                x = {"+": a.x + b.x, "-": a.x - b.x, "*": a.x * b.x, "/": a.x / b.x}[op]
            except ZeroDivisionError as error:
                raise MidasError("bad_rule", f"Division by zero in '{node.src}'.") from error
            return V(x=x)
        sa, sb = _align(a, b)
        if op == "+":
            out = sa + sb
        elif op == "-":
            out = sa - sb
        elif op == "*":
            out = sa * sb
        else:
            out = sa / sb.replace(0.0, np.nan)
        return V(s=out, ppy=a.ppy if a.is_series else b.ppy)

    def _call(self, node: Node) -> V:
        name, p = node.name, node.params
        if name in FIELD_FUNCS:
            if node.args[0].kind != "sym":
                raise MidasError("bad_rule", f"{name}() takes a symbol, got '{node.args[0].src}'.", f"Example: {name}(aapl.us).")
            return self._series_arg(node.args[0], name)
        if name == "abs":
            v = self.num(node.args[0])
            return V(s=v.s.abs(), ppy=v.ppy) if v.is_series else V(x=abs(v.x))
        base = self._series_arg(node.args[0]) if node.args[0].kind == "sym" else self.num(node.args[0])
        if not base.is_series:
            raise MidasError("bad_rule", f"{name}() needs a series, got the number '{node.args[0].src}'.", "Pass a symbol or a series function.")
        s, ppy = base.s, base.ppy
        w = int(p[0]) if p else 0
        if name == "sma":
            out = ind.sma(s, w)
        elif name == "ema":
            out = ind.ema(s, w)
        elif name == "rsi":
            out = ind.rsi(s, w)
        elif name in ("roc", "ret"):
            out = ind.roc(s, w)
        elif name == "change":
            out = ind.change(s, w)
        elif name == "lag":
            out = ind.lag(s, w)
        elif name == "vol":
            out = ind.rolling_vol(s, w, ppy)
        elif name == "zscore":
            out = ind.zscore(s, w)
        elif name == "rmax":
            out = ind.rmax(s, w)
        elif name == "rmin":
            out = ind.rmin(s, w)
        elif name == "bb_upper":
            out = ind.bollinger(s, w, p[1], "upper")
        elif name == "bb_lower":
            out = ind.bollinger(s, w, p[1], "lower")
        elif name == "bb_mid":
            out = ind.bollinger(s, w, 2.0, "mid")
        elif name == "pctb":
            out = ind.bollinger(s, w, p[1], "pctb")
        elif name == "drawdown":
            out = ind.drawdown(s)
        elif name == "yoy":
            out = ind.yoy(s)
        else:  # pragma: no cover - parser rejects unknown names
            raise MidasError("bad_rule", f"Unknown function {name}.")
        return V(s=out, ppy=ppy)

    # -- booleans
    def boolean(self, node: Node) -> B:
        k = node.kind
        if k == "cmp":
            a, b = self.num(node.args[0]), self.num(node.args[1])
            op = node.op
            if not a.is_series and not b.is_series:
                val = {"<": a.x < b.x, "<=": a.x <= b.x, ">": a.x > b.x, ">=": a.x >= b.x, "==": a.x == b.x, "!=": a.x != b.x}[op]
                idx = pd.DatetimeIndex([pd.Timestamp("1970-01-01")])
                res = B(pd.Series([val], index=idx), pd.Series([True], index=idx))
                self.terms.append({"expr": node.src, "op": op, "lhs": a.x, "rhs": b.x, "value": bool(val)})
                return res
            sa, sb = _align(a, b)
            ok = sa.notna() & sb.notna()
            cmp = {"<": sa < sb, "<=": sa <= sb, ">": sa > sb, ">=": sa >= sb, "==": sa == sb, "!=": sa != sb}[op]
            res = B(cmp & ok, ok)
            last_ok = bool(ok.iloc[-1]) if len(ok) else False
            self.terms.append({"expr": node.src, "op": op, "lhs": a.last(), "rhs": b.last(),
                               "value": bool(res.val.iloc[-1]) if last_ok else None})
            return res
        if k == "call" and node.name in ("cross_above", "cross_below"):
            a, b = self.num(node.args[0]), self.num(node.args[1])
            if not a.is_series and not b.is_series:
                raise MidasError("bad_rule", f"{node.name}() needs at least one series.")
            sa, sb = _align(a, b)
            ok = sa.notna() & sb.notna() & sa.shift(1).notna() & sb.shift(1).notna()
            if node.name == "cross_above":
                val = (sa > sb) & (sa.shift(1) <= sb.shift(1))
            else:
                val = (sa < sb) & (sa.shift(1) >= sb.shift(1))
            res = B(val & ok, ok)
            last_ok = bool(ok.iloc[-1]) if len(ok) else False
            self.terms.append({"expr": node.src, "op": node.name, "lhs": a.last(), "rhs": b.last(),
                               "value": bool(res.val.iloc[-1]) if last_ok else None})
            return res
        if k == "not":
            inner = self.boolean(node.args[0])
            return B(~inner.val & inner.ok, inner.ok)
        if k in ("and", "or"):
            x, y = self.boolean(node.args[0]), self.boolean(node.args[1])
            idx = x.val.index.union(y.val.index)
            xv, xo = x.val.reindex(idx).ffill().fillna(False).astype(bool), x.ok.reindex(idx).ffill().fillna(False).astype(bool)
            yv, yo = y.val.reindex(idx).ffill().fillna(False).astype(bool), y.ok.reindex(idx).ffill().fillna(False).astype(bool)
            if k == "and":
                val = xv & yv
                ok = (xo & yo) | (xo & ~xv) | (yo & ~yv)
            else:
                val = xv | yv
                ok = (xo & yo) | (xo & xv) | (yo & yv)
            return B(val & ok, ok)
        raise MidasError("bad_rule", f"'{node.src}' is not a condition.",
                         "A rule must compare something, e.g. close(aapl.us) < 150, or combine comparisons with and/or.")

    def evaluate(self, src: str) -> dict[str, Any]:
        """Parse and evaluate one rule at the end of the (already sliced) data. Never raises for data problems."""
        self.terms, self.data_dates = [], {}
        try:
            node = parse(src)
        except RuleSyntaxError as error:
            return {"state": "error", "expr": src, "value": None, "message": error.message, "hint": error.hint, "terms": [],
                    "data_dates": {}, "snapshots": {}}
        try:
            result = self.boolean(node)
        except MidasError as error:
            return {"state": "no_data" if error.code in ("no_data", "symbol_not_found") else "error", "expr": src, "value": None,
                    "message": error.message, "hint": error.hint, "terms": list(self.terms), "data_dates": dict(self.data_dates),
                    "snapshots": dict(self.used)}
        if result.val.empty or not bool(result.ok.iloc[-1]):
            return {"state": "no_data", "expr": src, "value": None,
                    "message": "An input has no value at the last bar yet (not enough history for a window, or an empty series).",
                    "hint": "Fetch a longer range or use a shorter window.", "terms": list(self.terms),
                    "data_dates": dict(self.data_dates), "snapshots": dict(self.used)}
        value = bool(result.val.iloc[-1])
        return {"state": "tripped" if value else "clear", "expr": src, "value": value, "terms": list(self.terms),
                "data_dates": dict(self.data_dates), "snapshots": dict(self.used)}

    def number(self, src: str) -> dict[str, Any]:
        """Evaluate a numeric expression (used by evidence metrics): the value at the last bar."""
        self.data_dates = {}
        try:
            node = parse(src)
            v = self.num(node)
        except RuleSyntaxError as error:
            raise MidasError("bad_rule", error.message, error.hint) from error
        last = v.last()
        if last is None or (isinstance(last, float) and math.isnan(last)):
            raise MidasError("no_data", f"'{src}' has no value at the last bar.", "Fetch a longer range or use a shorter window.")
        return {"value": last, "data_dates": dict(self.data_dates), "snapshots": dict(self.used)}


_BARE = re.compile(r"^[A-Za-z_^][A-Za-z0-9_.^/:]*$")


def quote_symbol(symbol: str) -> str:
    """The symbol as it must be written in a rule: bare when the grammar allows it, else double-quoted."""
    return symbol if _BARE.match(symbol) and symbol.lower() not in KEYWORDS and symbol.lower() not in FUNCTIONS else f'"{symbol}"'
