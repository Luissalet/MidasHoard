"""Yahoo Finance through the optional yfinance package. Unofficial: terms forbid redistribution and may change."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import FetchResult, Provider, normalise, slice_range


def _yfinance():
    try:
        import yfinance  # type: ignore

        return yfinance
    except Exception:  # noqa: BLE001
        return None


class YahooProvider(Provider):
    id = "yahoo"
    name = "Yahoo Finance (unofficial)"
    terms = ("Unofficial access through the yfinance package: not an API offered for this use, personal use only, may "
             "break or be rate limited without notice. Prices are auto-adjusted for splits and dividends.")
    delay = "Typically 15 minutes for exchange quotes; daily bars end of day."
    license = "Yahoo terms of use; no redistribution."
    unofficial = True

    def available(self) -> tuple[bool, str]:
        return (True, "") if _yfinance() is not None else (False, "the optional 'yfinance' package is not installed (pip install yfinance)")

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        yf = _yfinance()
        if yf is None:
            raise MidasError("provider_unavailable", "yahoo needs the optional yfinance package.", "pip install yfinance", provider=self.id)
        if self.http.offline:
            raise MidasError("provider_unavailable", "yahoo: offline mode.", provider=self.id)
        iv = {"d": "1d", "w": "1wk", "m": "1mo"}.get(interval.lower())
        if iv is None:
            raise MidasError("invalid_request", f"yahoo interval '{interval}' unsupported.", "Use d, w or m.")
        try:
            raw = yf.Ticker(symbol).history(start=start, end=end, interval=iv, auto_adjust=True)
        except Exception as error:  # noqa: BLE001
            raise MidasError("provider_unavailable", f"yahoo failed: {type(error).__name__}: {error}", provider=self.id) from error
        if raw is None or raw.empty:
            raise MidasError("symbol_not_found", f"yahoo has no data for '{symbol}'.", provider=self.id)
        raw.columns = [str(c).lower() for c in raw.columns]
        df = slice_range(normalise(raw), start, end)
        freq = {"d": "D", "w": "W", "m": "M"}[interval.lower()]
        ccy = ""
        try:
            ccy = str(yf.Ticker(symbol).fast_info.get("currency") or "").upper()
        except Exception:  # noqa: BLE001
            pass
        return FetchResult(df=df, provider_symbol=symbol, currency=options.get("currency") or ccy, unit=options.get("unit") or ccy,
                           frequency=freq, adjusted=True, periods_per_year={"D": 252.0, "W": 52.0, "M": 12.0}[freq],
                           kind="ohlcv", label=symbol, note="Unofficial source; auto-adjusted prices.")
