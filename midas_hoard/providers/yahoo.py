"""Yahoo Finance through its public chart endpoint (no yfinance, no key). Unofficial: may change or throttle at any time."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import PPY, FetchResult, Provider, slice_range

HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9"}
INTERVALS = {"d": "1d", "w": "1wk", "m": "1mo"}
FREQ = {"d": "D", "w": "W", "m": "M"}
KINDS = {"EQUITY": "equity", "ETF": "etf", "INDEX": "index", "CURRENCY": "fx", "CRYPTOCURRENCY": "crypto", "FUTURE": "commodity",
         "MUTUALFUND": "fund"}


def _epoch(value: Optional[str], default: int) -> int:
    if not value:
        return default
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def _local_dates(stamps: list[int], meta: dict[str, Any]) -> pd.DatetimeIndex:
    """Session timestamps to the exchange's local calendar date (falls back to the fixed GMT offset)."""
    utc = pd.to_datetime(pd.Series(stamps, dtype="int64"), unit="s", utc=True)
    name = meta.get("exchangeTimezoneName")
    if name:
        try:
            return pd.DatetimeIndex(utc.dt.tz_convert(name).dt.tz_localize(None).dt.normalize())
        except Exception:  # noqa: BLE001 — unknown zone name or no tz database on this machine
            pass
    offset = int(meta.get("gmtoffset") or 0)
    return pd.DatetimeIndex((utc + timedelta(seconds=offset)).dt.tz_localize(None).dt.normalize())


class YahooProvider(Provider):
    id = "yahoo"
    name = "Yahoo Finance (unofficial)"
    terms = ("Unofficial use of the public chart endpoint that Yahoo Finance's own pages call: it is not an API offered for "
             "this, personal and research use only, no redistribution, and it can change or throttle without notice. "
             "Closes are adjusted for splits and dividends; dividends and splits are recorded in the snapshot.")
    delay = "Daily bars; the latest bar may be intraday. Exchange quotes are usually delayed 15 minutes."
    license = "Yahoo terms of use; personal use, no redistribution."
    unofficial = True
    intervals = ("d", "w", "m")

    def _get(self, path: str, params: dict[str, Any], ttl: Optional[int] = None):
        last: Optional[MidasError] = None
        for host in HOSTS:
            try:
                return self.http.get(host + path, params, provider=self.id, ttl=ttl, headers=HEADERS)
            except MidasError as error:
                if error.code == "rate_limited":
                    raise
                last = error
        assert last is not None
        raise last

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        response = self._get("/v1/finance/search", {"q": query, "quotesCount": min(max(limit, 1), 20), "newsCount": 0}, ttl=86400)
        if response.status_code != 200:
            return []
        try:
            quotes = json.loads(response.text).get("quotes", [])
        except ValueError:
            return []
        out = []
        for q in quotes:
            kind = KINDS.get(str(q.get("quoteType", "")).upper())
            if not q.get("symbol") or kind is None:
                continue
            out.append({"provider": self.id, "symbol": q["symbol"], "name": q.get("longname") or q.get("shortname") or "", "kind": kind,
                        "currency": "", "unit": "", "frequency": "D", "exchange": q.get("exchDisp", "")})
        return out[:limit]

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        key = interval.lower()
        iv = INTERVALS.get(key)
        if iv is None:
            raise MidasError("invalid_request", f"yahoo interval '{interval}' unsupported.", "Use d, w or m.")
        sym = symbol.strip()
        now = int(self.http.clock())
        params = {"period1": _epoch(start, 0), "period2": _epoch(end, now) + 86400 if end else now + 86400, "interval": iv,
                  "events": "div,splits"}
        response = self._get(f"/v8/finance/chart/{sym}", params)
        try:
            body = json.loads(response.text)
            chart = body["chart"]
        except (ValueError, KeyError, TypeError):
            raise MidasError("provider_unavailable", f"yahoo answered HTTP {response.status_code} without chart data.",
                             "Try again later; Yahoo may be throttling.", provider=self.id) from None
        error = chart.get("error")
        if error or response.status_code == 404 or not chart.get("result"):
            what = (error or {}).get("description") or "no data"
            raise MidasError("symbol_not_found", f"yahoo has no data for '{symbol}' ({what}).",
                             "Yahoo symbols look like AAPL, ^GSPC, ^IBEX, SAN.MC, EURUSD=X, BTC-EUR. Use market_search.", provider=self.id)
        if response.status_code != 200:
            raise MidasError("provider_unavailable", f"yahoo answered HTTP {response.status_code}.", provider=self.id)
        result = chart["result"][0]
        meta = result.get("meta") or {}
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not stamps or not quote.get("close"):
            raise MidasError("no_data", f"yahoo returned no bars for '{symbol}' in the requested range.", "Widen start/end.", provider=self.id)
        frame = pd.DataFrame({c: quote.get(c) or [None] * len(stamps) for c in ("open", "high", "low", "close", "volume")},
                             index=_local_dates(stamps, meta)).apply(pd.to_numeric, errors="coerce")
        adj = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose")
        adjusted = bool(adj) and len(adj) == len(stamps)
        if adjusted:
            adjc = pd.to_numeric(pd.Series(adj, index=frame.index), errors="coerce")
            factor = (adjc / frame["close"]).where(frame["close"] > 0)
            for c in ("open", "high", "low"):
                frame[c] = frame[c] * factor
            frame["close"] = adjc
        frame = frame[frame["close"].notna()]
        frame = frame[~frame.index.duplicated(keep="last")].sort_index().astype("float64")
        frame.index.name = "date"
        df = slice_range(frame, start, end)
        if df.empty:
            raise MidasError("no_data", f"yahoo returned no rows for '{symbol}' in the requested range.", "Widen start/end.", provider=self.id)
        events = result.get("events") or {}
        splits = [{"date": datetime.fromtimestamp(int(v["date"]), timezone.utc).strftime("%Y-%m-%d"), "ratio": v.get("splitRatio") or
                   f"{v.get('numerator')}:{v.get('denominator')}"} for v in (events.get("splits") or {}).values() if v.get("date")]
        dividends = [{"date": datetime.fromtimestamp(int(v["date"]), timezone.utc).strftime("%Y-%m-%d"), "amount": v.get("amount")}
                     for v in (events.get("dividends") or {}).values() if v.get("date")]
        splits.sort(key=lambda e: e["date"])
        dividends.sort(key=lambda e: e["date"])
        kind_index = str(meta.get("instrumentType", "")).upper() == "INDEX"
        raw_ccy = str(meta.get("currency") or "")
        unit = ""
        if kind_index:
            currency, unit = "", "index points"
        elif raw_ccy == "GBp":
            currency, unit = "GBX", "GBX (pence)"
        else:
            currency = raw_ccy.upper()
            unit = currency
        currency = options.get("currency") or currency
        unit = options.get("unit") or unit
        extra: dict[str, Any] = {"exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
                                 "instrument_type": meta.get("instrumentType", ""), "timezone": meta.get("exchangeTimezoneName", ""),
                                 "splits": splits[-50:], "dividends": dividends[-200:]}
        if str(meta.get("instrumentType", "")).upper() == "CURRENCY" and len(sym) >= 6:
            extra.update({"base": sym[:3].upper(), "quote": sym[3:6].upper()})
            if not options.get("unit"):
                unit = f"{sym[3:6].upper()} per {sym[:3].upper()}"
        freq = FREQ[key]
        note = (f"Unofficial source; closes adjusted for splits and dividends ({len(splits)} splits, {len(dividends)} dividends in range). "
                + ("Open/high/low are scaled by the same factor. " if adjusted else "No adjusted close was provided; prices are raw. ")
                + ("London quotes are in pence (GBX). " if raw_ccy == "GBp" else ""))
        return FetchResult(df=df, provider_symbol=sym, currency=currency, unit=unit, frequency=freq, adjusted=adjusted,
                           periods_per_year=PPY[freq], kind="ohlcv", label=str(meta.get("longName") or meta.get("shortName") or sym),
                           note=note.strip(), extra=extra, cached=response.cached)
