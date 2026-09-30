"""Alpha Vantage daily series (free API key required; off until a key is configured). Credit: Alpha Vantage."""

from __future__ import annotations

import json
from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import PPY, FetchResult, Provider, normalise, slice_range

URL = "https://www.alphavantage.co/query"


class AlphaVantageProvider(Provider):
    id = "alphavantage"
    name = "Alpha Vantage"
    needs_key = True
    terms = ("Free tier with your own API key (https://www.alphavantage.co/support/#api-key): a few requests per minute and a small "
             "daily allowance; TIME_SERIES_DAILY gives raw (not split-adjusted) daily bars. Personal use under the provider's terms.")
    delay = "End of day."
    license = "Alpha Vantage terms of service; attribution to Alpha Vantage."
    intervals = ("d",)

    def available(self) -> tuple[bool, str]:
        if self.key():
            return True, ""
        return False, "needs a free API key: set MIDAS_ALPHAVANTAGE_KEY or save it in Settings"

    def _query(self, symbol: str, size: str):
        return self.http.get(URL, {"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": size, "apikey": self.key()}, provider=self.id)

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        if not self.key():
            raise MidasError("provider_unavailable", "alphavantage needs an API key.", "Set MIDAS_ALPHAVANTAGE_KEY or save it in Settings.", provider=self.id)
        if interval.lower() != "d":
            raise MidasError("invalid_request", "alphavantage supports daily data only here.", "Use interval d.")
        sym = symbol.strip().upper()
        full = bool(start) and (pd.Timestamp.now().normalize() - pd.Timestamp(start)).days > 120
        note_extra = ""
        response = self._query(sym, "full" if full else "compact")
        body = self._json(response.text)
        info = str(body.get("Information") or "")
        if full and "premium" in info.lower():
            response = self._query(sym, "compact")
            body = self._json(response.text)
            info = str(body.get("Information") or "")
            note_extra = " Free keys get only the latest ~100 bars."
        if body.get("Error Message"):
            raise MidasError("symbol_not_found", f"alphavantage has no data for '{symbol}'.", "Use the ticker as listed, e.g. IBM or SAN.MAD.", provider=self.id)
        if body.get("Note") or info:
            raise MidasError("rate_limited", "alphavantage limit reached or the request needs a premium key.",
                             "Wait a minute (or until tomorrow) and retry; cached data is reused.", provider=self.id)
        series = body.get("Time Series (Daily)")
        if not isinstance(series, dict) or not series:
            raise MidasError("provider_unavailable", "alphavantage answered without a daily series.", provider=self.id)
        rows = {d: {"open": v.get("1. open"), "high": v.get("2. high"), "low": v.get("3. low"), "close": v.get("4. close"),
                    "volume": v.get("5. volume")} for d, v in series.items()}
        frame = pd.DataFrame.from_dict(rows, orient="index")
        frame.index = pd.to_datetime(frame.index, errors="coerce")
        df = slice_range(normalise(frame), start, end)
        if df.empty:
            raise MidasError("no_data", f"alphavantage returned no rows for '{symbol}' in the requested range.", "Widen start/end.", provider=self.id)
        ccy = str(options.get("currency") or "")
        return FetchResult(df=df, provider_symbol=sym, currency=ccy, unit=options.get("unit") or ccy, frequency="D", adjusted=False,
                           periods_per_year=PPY["D"], kind="ohlcv", label=sym, cached=response.cached,
                           note=("Raw daily bars (not split-adjusted). Currency is not declared by the provider; pass currency=." + note_extra))

    @staticmethod
    def _json(text: str) -> dict[str, Any]:
        try:
            data = json.loads(text)
        except ValueError:
            raise MidasError("provider_unavailable", "alphavantage answered with something that is not JSON.", provider="alphavantage") from None
        return data if isinstance(data, dict) else {}
