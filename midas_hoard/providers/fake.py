"""Deterministic synthetic series for tests and demos. Never mistaken for market data: the terms say so."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

import numpy as np
import pandas as pd

from .base import FetchResult, Provider, normalise, slice_range

FIRST, LAST = "2015-01-01", "2024-12-31"
PRESETS = {
    "fake.up": (0.0006, 0.010),
    "fake.down": (-0.0006, 0.010),
    "fake.flat": (0.0, 0.008),
    "fake.vol": (0.0002, 0.030),
}


def _rng(symbol: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(symbol.lower().encode("utf-8")).hexdigest()[:8], 16))


class FakeProvider(Provider):
    id = "fake"
    name = "Synthetic (fake)"
    terms = "Deterministic synthetic data generated locally from the symbol name. Not market data; for tests and demos only."
    delay = "None (generated); covers 2015-01-01 to 2024-12-31."
    license = "Generated."
    needs_network = False
    intervals = ("d",)

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = query.lower()
        names = list(PRESETS) + ["fakefx.eurusd", "fakemacro.cpi"]
        return [{"provider": "fake", "symbol": n, "name": f"Synthetic {n}", "kind": "synthetic", "currency": "USD", "unit": "USD",
                 "frequency": "M" if n.startswith("fakemacro") else "D"} for n in names if q in n][:limit]

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        sym = symbol.strip().lower()
        rng = _rng(sym)
        extra: dict[str, Any] = {}
        unit = options.get("unit") or ""
        currency = str(options.get("currency") or "USD").upper()
        if sym.startswith("fakemacro."):
            idx = pd.date_range(FIRST, LAST, freq="MS")
            growth = rng.normal(0.003, 0.0015, len(idx))
            level = 100.0 * np.cumprod(1.0 + growth)
            df = normalise(pd.DataFrame({"close": level}, index=idx), ["close"])
            df = slice_range(df, start, end)
            return FetchResult(df=df, provider_symbol=sym, currency="", unit=unit or "index", frequency="M", adjusted=False,
                               periods_per_year=12.0, kind="series", label=sym, note="Synthetic monthly index.")
        idx = pd.bdate_range(FIRST, LAST)
        n = len(idx)
        if sym.startswith("fakefx."):
            pair = sym.split(".", 1)[1].upper()
            base, quote = (pair[:3], pair[3:6]) if len(pair) >= 6 else ("EUR", "USD")
            rate = 1.1 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
            df = normalise(pd.DataFrame({"close": rate}, index=idx), ["close"])
            df = slice_range(df, start, end)
            return FetchResult(df=df, provider_symbol=sym, currency=quote, unit=f"{quote} per {base}", frequency="D",
                               adjusted=False, periods_per_year=252.0, kind="series", label=sym,
                               extra={"base": base, "quote": quote}, note="Synthetic exchange rate.")
        drift, vol = PRESETS.get(sym, (0.0002, 0.012))
        rets = rng.normal(drift, vol, n)
        close = 100.0 * np.cumprod(1.0 + rets)
        prev = np.concatenate([[100.0], close[:-1]])
        open_ = prev * (1.0 + rng.normal(0.0, vol * 0.3, n))
        spread = np.abs(rng.normal(0.0, vol * 0.5, n))
        high = np.maximum(open_, close) * (1.0 + spread)
        low = np.minimum(open_, close) * (1.0 - spread)
        volume = rng.integers(100_000, 5_000_000, n).astype("float64")
        df = normalise(pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx))
        df = slice_range(df, start, end)
        return FetchResult(df=df, provider_symbol=sym, currency=currency, unit=unit or currency, frequency="D",
                           adjusted=bool(options.get("adjusted", False)), periods_per_year=252.0, kind="ohlcv", label=sym,
                           note="Synthetic random walk; not market data.")
