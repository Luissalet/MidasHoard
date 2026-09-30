"""CoinGecko free API: daily crypto prices, rate-limit aware."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd

from ..errors import MidasError
from .base import FetchResult, HttpClient, Provider, normalise, slice_range

BASE = "https://api.coingecko.com/api/v3"
MIN_INTERVAL_S = 2.5  # the public tier allows roughly 5-15 calls a minute


class CoinGeckoProvider(Provider):
    id = "coingecko"
    name = "CoinGecko"
    terms = ("CoinGecko public API (free tier, attribution to CoinGecko required, rate limited). The free tier "
             "serves at most the last 365 days of history. Prices are aggregated across exchanges.")
    delay = "Near real time; the current day is partial and is dropped from daily snapshots."
    license = "CoinGecko API terms; attribute 'Data provided by CoinGecko'."

    def __init__(self, http: HttpClient, *, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        super().__init__(http)
        self._clock = clock
        self._sleep = sleep
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = MIN_INTERVAL_S - (self._clock() - self._last_call)
        if wait > 0 and self._last_call:
            self._sleep(wait)
        self._last_call = self._clock()

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self._throttle()
        response = self.http.get(f"{BASE}/search", {"query": query}, provider=self.id, ttl=86400)
        if response.status_code != 200:
            return []
        try:
            import json

            coins = json.loads(response.text).get("coins", [])
        except ValueError:
            return []
        return [{"provider": self.id, "symbol": c["id"], "name": c.get("name", ""), "kind": "crypto", "currency": "EUR",
                 "unit": "EUR", "frequency": "D"} for c in coins[:limit]]

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        coin, _, quote = symbol.strip().lower().partition(":")
        vs = (options.get("currency") or quote or "eur").lower()
        today = datetime.fromtimestamp(self._clock(), tz=timezone.utc).date()
        begin = pd.Timestamp(start).date() if start else today.replace(year=today.year - 1)
        days = max(1, (today - begin).days + 1)
        if days > 365:
            days = 365
        self._throttle()
        response = self.http.get(f"{BASE}/coins/{coin}/market_chart", {"vs_currency": vs, "days": days, "interval": "daily"},
                                 provider=self.id, ttl=3600)
        if response.status_code == 404:
            raise MidasError("symbol_not_found", f"CoinGecko has no coin '{coin}'.", "Use the coin id (bitcoin, ethereum). Try market_search.",
                             provider=self.id)
        if response.status_code == 401 or response.status_code == 403:
            raise MidasError("provider_unavailable", "CoinGecko refused the request (history beyond the free tier?).",
                             "Ask for at most 365 days.", provider=self.id)
        if response.status_code != 200:
            raise MidasError("provider_unavailable", f"CoinGecko answered HTTP {response.status_code}.", provider=self.id)
        import json

        try:
            body = json.loads(response.text)
            prices = body["prices"]
        except (ValueError, KeyError) as error:
            raise MidasError("provider_unavailable", "CoinGecko returned an unexpected body.", provider=self.id) from error
        idx = pd.to_datetime([p[0] for p in prices], unit="ms", utc=True).tz_localize(None)
        frame = pd.DataFrame({"close": [p[1] for p in prices]}, index=idx)
        volumes = body.get("total_volumes") or []
        if len(volumes) == len(prices):
            frame["volume"] = [v[1] for v in volumes]
        df = normalise(frame, ["close", "volume"])
        dropped = ""
        if len(df) and df.index[-1].date() >= today:
            df = df.iloc[:-1]
            dropped = " The partial current day was dropped."
        df = slice_range(df, start, end)
        if df.empty:
            raise MidasError("no_data", f"CoinGecko returned no rows for '{coin}' in the requested range.", "Widen start/end.", provider=self.id)
        return FetchResult(df=df, provider_symbol=f"{coin}:{vs}", currency=vs.upper(), unit=vs.upper(), frequency="D",
                           adjusted=False, periods_per_year=365.0, kind="series", label=f"{coin} ({vs.upper()})",
                           note="Data provided by CoinGecko. Daily points are taken at 00:00 UTC." + dropped, cached=response.cached)
