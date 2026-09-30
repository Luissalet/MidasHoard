"""FRED (Federal Reserve Bank of St. Louis) macro series through the public CSV endpoint. Credit: FRED."""

from __future__ import annotations

import io
from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import PPY, FetchResult, Provider, infer_frequency, normalise, slice_range

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class FredProvider(Provider):
    id = "fred"
    name = "FRED"
    terms = ("Source: Federal Reserve Bank of St. Louis, FRED(R), https://fred.stlouisfed.org. Some series carry "
             "third-party copyright; check the series page before redistributing. Values are as last revised: "
             "they are not vintage (point-in-time) data, so revisions can leak into past dates.")
    delay = "Varies per series (daily to quarterly); releases lag the reference period."
    license = "Public FRED terms; attribution required."
    intervals = ("d",)

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        sid = symbol.strip().upper()
        params: dict[str, Any] = {"id": sid}
        response = self.http.get(URL, params, provider=self.id)
        text = response.text.strip()
        head = text[:200].lower()
        if response.status_code in (400, 404) or not text or "<html" in head or "error" in head.split("\n", 1)[0]:
            raise MidasError("symbol_not_found", f"FRED has no series '{sid}'.", "Series ids look like CPIAUCSL, UNRATE, DGS10.", provider=self.id)
        if response.status_code != 200:
            raise MidasError("provider_unavailable", f"FRED answered HTTP {response.status_code}.", provider=self.id)
        raw = pd.read_csv(io.StringIO(text))
        if raw.shape[1] < 2:
            raise MidasError("provider_unavailable", "FRED CSV without a value column.", provider=self.id)
        date_col, value_col = raw.columns[0], raw.columns[1]
        frame = pd.DataFrame({"close": pd.to_numeric(raw[value_col], errors="coerce").values},
                             index=pd.to_datetime(raw[date_col], errors="coerce"))
        df = slice_range(normalise(frame, ["close"]), start, end)
        if df.empty:
            raise MidasError("no_data", f"FRED returned no observations for '{sid}' in the requested range.", "Widen start/end.", provider=self.id)
        freq = options.get("frequency") or infer_frequency(df.index)
        return FetchResult(df=df, provider_symbol=sid, currency=options.get("currency", ""), unit=options.get("unit", ""),
                           frequency=freq, adjusted=False, periods_per_year=PPY.get(freq, 252.0), kind="series", label=sid,
                           note="Missing observations ('.') are dropped. Revised values, not vintages.", cached=response.cached)
