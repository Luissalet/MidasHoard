"""User CSV import with an explicit column mapping and a declared currency/unit."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ..errors import MidasError
from .base import OHLCV, PPY, FetchResult, Provider, infer_frequency, normalise, slice_range

MAX_BYTES = 50 * 1024 * 1024
ALIASES = {
    "date": ("date", "datetime", "time", "timestamp", "observation_date", "fecha", "day"),
    "close": ("close", "adj close", "adj_close", "adjclose", "value", "price", "last", "cierre", "valor"),
    "open": ("open", "apertura"),
    "high": ("high", "max", "máximo", "maximo"),
    "low": ("low", "min", "mínimo", "minimo"),
    "volume": ("volume", "vol", "volumen"),
}


def _detect(columns: list[str], want: str) -> Optional[str]:
    lowered = {c.strip().lower(): c for c in columns}
    for alias in ALIASES[want]:
        if alias in lowered:
            return lowered[alias]
    return None


class CsvProvider(Provider):
    id = "csv"
    name = "CSV import"
    terms = "Your own file: you are responsible for the right to use the data. Currency and unit are what you declare."
    delay = "Whatever your file holds."
    license = "User data."
    needs_network = False

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        path, text = options.get("path"), options.get("csv_text")
        if not path and not text:
            raise MidasError("invalid_request", "csv import needs 'path' (a file) or 'csv_text' (the CSV content).",
                             "Pass options path=... or csv_text=...")
        if path:
            file = Path(str(path)).expanduser()
            if not file.is_file():
                raise MidasError("not_found", f"CSV file not found: {file}", "Give an absolute path to an existing .csv file.")
            if file.stat().st_size > MAX_BYTES:
                raise MidasError("invalid_request", "CSV larger than 50 MB.", "Split the file or reduce the range.")
            text = file.read_text(encoding=options.get("encoding") or "utf-8-sig")
        if not options.get("currency") and not options.get("unit"):
            raise MidasError("declaration_required", "Declare the currency and/or the unit of the imported series.",
                             "Pass currency (e.g. EUR) for prices or unit (e.g. '% per year', 'index points') for other series. "
                             "Nothing is guessed.")
        decimal = options.get("decimal") or "."
        try:
            raw = pd.read_csv(io.StringIO(str(text)), sep=options.get("delimiter") or None, engine="python", decimal=decimal,
                              thousands="." if decimal == "," else None)
        except Exception as error:  # noqa: BLE001
            raise MidasError("invalid_request", f"Could not parse the CSV: {error}", "Check the delimiter and header row.") from error
        cols = [str(c) for c in raw.columns]
        mapping: dict[str, str] = {k: v for k, v in (options.get("mapping") or {}).items() if v}
        for want in ("date", "close", "open", "high", "low", "volume"):
            if want not in mapping:
                found = _detect(cols, want)
                if found:
                    mapping[want] = found
        for need in ("date", "close"):
            if need not in mapping:
                raise MidasError("invalid_request", f"No '{need}' column found (columns: {', '.join(cols)}).",
                                 f"Pass mapping={{\"{need}\": \"<column name>\"}}.")
        for want, col in mapping.items():
            if col not in raw.columns:
                raise MidasError("invalid_request", f"Mapped column '{col}' for '{want}' is not in the file.", f"Columns: {', '.join(cols)}.")
        dates = pd.to_datetime(raw[mapping["date"]], errors="coerce", dayfirst=bool(options.get("dayfirst")))
        if dates.isna().all():
            raise MidasError("invalid_request", "The date column could not be parsed.", "Use ISO dates (YYYY-MM-DD) or pass dayfirst=true.")
        frame = pd.DataFrame({col_name: pd.to_numeric(raw[src], errors="coerce").values
                              for col_name, src in mapping.items() if col_name != "date"}, index=dates)
        df = slice_range(normalise(frame, [c for c in OHLCV if c in frame.columns]), start, end)
        if df.empty:
            raise MidasError("no_data", "The CSV has no usable rows in the requested range.", "Check the date format and the range.")
        freq = options.get("frequency") or infer_frequency(df.index)
        has_ohlc = all(c in df.columns for c in ("open", "high", "low"))
        return FetchResult(df=df, provider_symbol=symbol, currency=str(options.get("currency") or "").upper(),
                           unit=str(options.get("unit") or ""), frequency=freq, adjusted=bool(options.get("adjusted", False)),
                           periods_per_year=float(options.get("periods_per_year") or PPY.get(freq, 252.0)),
                           kind="ohlcv" if has_ohlc else "series", label=symbol,
                           note=f"Imported from {'file ' + Path(str(path)).name if path else 'pasted text'}; columns mapped: {mapping}.")
