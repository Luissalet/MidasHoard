"""Tiingo end-of-day prices (free API key required; off until a key is configured). Credit: Tiingo."""

from __future__ import annotations

import json
from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import PPY, FetchResult, Provider, slice_range

URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"


class TiingoProvider(Provider):
    id = "tiingo"
    name = "Tiingo"
    needs_key = True
    terms = ("Free tier with your own API token (https://www.tiingo.com): hourly and daily request limits, mostly US equities and "
             "ETFs. Prices are adjusted for splits and dividends (adjClose and friends). Personal use under the provider's terms.")
    delay = "End of day."
    license = "Tiingo terms of service; attribution to Tiingo."
    intervals = ("d",)

    def available(self) -> tuple[bool, str]:
        if self.key():
            return True, ""
        return False, "needs a free API key: set MIDAS_TIINGO_KEY or save it in Settings"

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        if not self.key():
            raise MidasError("provider_unavailable", "tiingo needs an API key.", "Set MIDAS_TIINGO_KEY or save it in Settings.", provider=self.id)
        if interval.lower() != "d":
            raise MidasError("invalid_request", "tiingo supports daily data only here.", "Use interval d.")
        sym = symbol.strip().lower()
        params: dict[str, Any] = {"startDate": start or "1970-01-01", "format": "json", "resampleFreq": "daily"}
        if end:
            params["endDate"] = end
        response = self.http.get(URL.format(ticker=sym), params, provider=self.id,
                                 headers={"Authorization": f"Token {self.key()}", "Content-Type": "application/json"})
        if response.status_code == 401 or response.status_code == 403:
            raise MidasError("provider_unavailable", "tiingo rejected the API token.", "Check the key in Settings.", provider=self.id)
        try:
            data = json.loads(response.text)
        except ValueError:
            data = None
        if response.status_code == 404 or (isinstance(data, dict) and "not found" in str(data.get("detail", "")).lower()):
            raise MidasError("symbol_not_found", f"tiingo has no data for '{symbol}'.", "Use a ticker such as aapl or spy.", provider=self.id)
        if isinstance(data, dict):
            raise MidasError("provider_unavailable", f"tiingo: {str(data.get('detail', 'unexpected answer'))[:120]}", provider=self.id)
        if not isinstance(data, list):
            raise MidasError("provider_unavailable", f"tiingo answered HTTP {response.status_code} without prices.", provider=self.id)
        if not data:
            raise MidasError("no_data", f"tiingo returned no rows for '{symbol}' in the requested range.", "Widen start/end.", provider=self.id)
        frame = pd.DataFrame({"open": [r.get("adjOpen") for r in data], "high": [r.get("adjHigh") for r in data],
                              "low": [r.get("adjLow") for r in data], "close": [r.get("adjClose") for r in data],
                              "volume": [r.get("adjVolume", r.get("volume")) for r in data]},
                             index=pd.to_datetime([str(r.get("date", ""))[:10] for r in data], errors="coerce"))
        frame = frame.apply(pd.to_numeric, errors="coerce")
        frame = frame[frame.index.notna() & frame["close"].notna()]
        frame = frame[~frame.index.duplicated(keep="last")].sort_index().astype("float64")
        frame.index.name = "date"
        df = slice_range(frame, start, end)
        if df.empty:
            raise MidasError("no_data", f"tiingo returned no rows for '{symbol}' in the requested range.", "Widen start/end.", provider=self.id)
        ccy = str(options.get("currency") or "USD")
        splits = [{"date": str(r["date"])[:10], "ratio": r["splitFactor"]} for r in data if r.get("splitFactor") not in (None, 1, 1.0)]
        divs = [{"date": str(r["date"])[:10], "amount": r["divCash"]} for r in data if r.get("divCash") not in (None, 0, 0.0)]
        return FetchResult(df=df, provider_symbol=sym, currency=ccy, unit=options.get("unit") or ccy, frequency="D", adjusted=True,
                           periods_per_year=PPY["D"], kind="ohlcv", label=sym.upper(), cached=response.cached,
                           note="Adjusted for splits and dividends; US-listed tickers are quoted in USD.",
                           extra={"splits": splits[-50:], "dividends": divs[-200:]})
