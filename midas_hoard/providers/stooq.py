"""Stooq: free daily OHLCV as CSV, no key."""

from __future__ import annotations

import io
from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import FetchResult, Provider, normalise, slice_range

URL = "https://stooq.com/q/d/l/"
INTERVALS = {"d": "d", "w": "w", "m": "m", "q": "q", "y": "y"}
FREQ = {"d": "D", "w": "W", "m": "M", "q": "Q", "y": "A"}
SUFFIX_CCY = {"us": "USD", "uk": "GBP", "de": "EUR", "pl": "PLN", "hu": "HUF", "jp": "JPY", "hk": "HKD", "f": "USD"}


def classify(symbol: str) -> dict[str, Any]:
    """Currency/unit guesses from a Stooq symbol. Unknown stays empty: the snapshot says so instead of guessing."""
    s = symbol.lower()
    if s.startswith("^"):
        return {"currency": "", "unit": "index points", "note": "Index level: no currency attached."}
    if "." in s:
        suffix = s.rsplit(".", 1)[1]
        ccy = SUFFIX_CCY.get(suffix, "")
        unit = "GBX (pence)" if suffix == "uk" else ccy
        note = "" if ccy else "Currency not declared by the provider; pass currency= to set it."
        if suffix == "uk":
            note = "London quotes are in pence (GBX); divide by 100 for GBP."
        return {"currency": ccy, "unit": unit, "note": note}
    if len(s) == 6 and s.isalpha():
        base, quote = s[:3].upper(), s[3:].upper()
        return {"currency": quote, "unit": f"{quote} per {base}", "extra": {"base": base, "quote": quote}, "note": ""}
    return {"currency": "", "unit": "", "note": "Currency not declared by the provider; pass currency= to set it."}


class StooqProvider(Provider):
    id = "stooq"
    name = "Stooq"
    terms = ("Free end-of-day data from stooq.com for personal, non-commercial use; no redistribution. "
             "Adjustment treatment is not documented by the provider, so snapshots are recorded as adjusted=false.")
    delay = "End of day; typically available after the session close."
    license = "Provider terms apply (personal use)."
    intervals = ("d", "w", "m", "q", "y")
    blocked = ("Stooq now answers scripts with a JavaScript browser check, so it cannot be fetched automatically. "
               "Use yahoo or a keyed provider, or import the CSV you downloaded by hand with the csv provider.")

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        iv = INTERVALS.get(interval.lower())
        if iv is None:
            raise MidasError("invalid_request", f"stooq does not support interval '{interval}'.", f"Use one of {', '.join(INTERVALS)}.")
        sym = symbol.strip().lower()
        params: dict[str, Any] = {"s": sym, "i": iv}
        response = self.http.get(URL, params, provider=self.id)
        text = response.text.strip()
        head = text[:300].lower()
        if "text/html" in response.content_type.lower() or head.startswith(("<!doctype", "<html")) or "<html" in head:
            raise MidasError("provider_unavailable", "stooq answered with a browser-check page (JavaScript required), not data.",
                             "Stooq now requires a browser check; use yahoo or a keyed provider.", provider=self.id)
        if response.status_code == 404 or not text or text.lower().startswith("no data"):
            if "apikey" in text.lower() or "captcha" in text.lower():
                raise MidasError("provider_unavailable", "stooq asks for an API key or captcha for this request.",
                                 "Open stooq.com in a browser, or import the series with the csv provider.", provider=self.id)
            raise MidasError("symbol_not_found", f"stooq has no data for '{symbol}'.",
                             "Symbols look like aapl.us, ^spx, eurusd. Use market_search.", provider=self.id)
        if not text.lower().startswith("date"):
            raise MidasError("provider_unavailable", f"stooq returned an unexpected body: {text[:80]!r}.", provider=self.id)
        raw = pd.read_csv(io.StringIO(text))
        raw.columns = [str(c).strip().lower() for c in raw.columns]
        if "date" not in raw.columns or "close" not in raw.columns:
            raise MidasError("provider_unavailable", "stooq CSV without Date/Close columns.", provider=self.id)
        raw = raw.set_index(pd.to_datetime(raw["date"], errors="coerce"))
        df = slice_range(normalise(raw), start, end)
        if df.empty:
            raise MidasError("no_data", f"stooq returned no rows for '{symbol}' in the requested range.", "Widen start/end.", provider=self.id)
        info = classify(sym)
        currency = options.get("currency") or info["currency"]
        unit = options.get("unit") or info["unit"]
        freq = FREQ[iv]
        ppy = {"D": 252.0, "W": 52.0, "M": 12.0, "Q": 4.0, "A": 1.0}[freq]
        return FetchResult(df=df, provider_symbol=sym, currency=currency, unit=unit, frequency=freq, adjusted=False,
                           periods_per_year=ppy, kind="ohlcv", label=sym, note=info.get("note", ""),
                           extra=info.get("extra", {}), cached=response.cached)
