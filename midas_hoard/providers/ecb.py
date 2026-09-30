"""ECB Data Portal (SDMX CSV): euro foreign exchange reference rates and other flows."""

from __future__ import annotations

import io
from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import PPY, FetchResult, Provider, normalise, slice_range

BASE = "https://data-api.ecb.europa.eu/service/data/"
FREQ = {"D": "D", "B": "D", "W": "W", "M": "M", "Q": "Q", "A": "A"}


class EcbProvider(Provider):
    id = "ecb"
    name = "ECB Data Portal"
    terms = ("European Central Bank statistics: reuse permitted with attribution ('Source: ECB'). Euro reference "
             "rates are indicative, published around 16:00 CET, and not meant for transactions.")
    delay = "Daily reference rates, published once per TARGET working day around 16:00 CET."
    license = "ECB reuse policy; acknowledge the source."

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        key = symbol.strip().strip("/")
        if "/" not in key:
            raise MidasError("invalid_request", f"ecb symbols are '<flow>/<key>', got '{symbol}'.",
                             "Example: EXR/D.USD.EUR.SP00.A (US dollar per euro, daily).")
        params: dict[str, Any] = {"format": "csvdata"}
        if start:
            params["startPeriod"] = start
        if end:
            params["endPeriod"] = end
        response = self.http.get(BASE + key, params, provider=self.id)
        text = response.text.strip()
        if response.status_code == 404 or (response.status_code == 200 and not text):
            raise MidasError("symbol_not_found", f"The ECB has no series '{key}'" + (" in that range." if start or end else "."),
                             "Check the flow and key in the ECB Data Portal (SDMX).", provider=self.id)
        if response.status_code != 200:
            raise MidasError("provider_unavailable", f"ECB answered HTTP {response.status_code}.", provider=self.id)
        raw = pd.read_csv(io.StringIO(text))
        if "TIME_PERIOD" not in raw.columns or "OBS_VALUE" not in raw.columns:
            raise MidasError("provider_unavailable", "ECB CSV without TIME_PERIOD/OBS_VALUE columns.", provider=self.id)
        raw = raw[raw["OBS_VALUE"].notna()]
        period = raw["TIME_PERIOD"].astype(str)
        dates = pd.to_datetime(period.where(period.str.len() > 7, period + "-01"), errors="coerce")
        frame = pd.DataFrame({"close": pd.to_numeric(raw["OBS_VALUE"], errors="coerce").values}, index=dates)
        df = slice_range(normalise(frame, ["close"]), start, end)
        if df.empty:
            raise MidasError("no_data", f"ECB returned no observations for '{key}'.", "Widen start/end.", provider=self.id)
        first = raw.iloc[0]
        freq = FREQ.get(str(first.get("FREQ", key.split(".")[0].split("/")[-1])).upper()[:1], "D")
        flow = key.split("/")[0].upper()
        currency = unit = ""
        extra: dict[str, Any] = {}
        if flow == "EXR" and "CURRENCY" in raw.columns and "CURRENCY_DENOM" in raw.columns:
            quote, base = str(first["CURRENCY"]).upper(), str(first["CURRENCY_DENOM"]).upper()
            currency, unit = quote, f"{quote} per {base}"
            extra = {"base": base, "quote": quote}
        elif "UNIT" in raw.columns:
            unit = str(first["UNIT"])
        currency = options.get("currency") or currency
        unit = options.get("unit") or unit
        return FetchResult(df=df, provider_symbol=key, currency=currency, unit=unit, frequency=freq, adjusted=False,
                           periods_per_year=PPY.get(freq, 252.0), kind="series", label=key, extra=extra,
                           note="Source: ECB Data Portal.", cached=response.cached)
