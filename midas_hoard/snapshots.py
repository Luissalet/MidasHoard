"""Frozen, provenance-tagged market data.

Every fetch becomes a *snapshot*: the normalised rows are written once to ``data/snapshots/<id>.csv`` and never
touched again; the row holds provider, symbol, fetch time, requested and actual range, frequency, currency,
adjustment, unit, row count and the sha256 of the stored bytes. Analyses and experiments cite snapshot ids, so a
result can always be reproduced from the same bytes — and a file edited behind the app's back is detected on load.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from .db import Database
from .errors import MidasError
from .hoard_link.atomic import write_text_atomic
from .providers.base import OHLCV, FetchResult, Provider


def canonical_csv(df: pd.DataFrame) -> str:
    """The exact text that is stored and hashed: ISO dates, shortest round-trip floats, empty for NaN."""
    cols = [c for c in OHLCV if c in df.columns]
    lines = ["date," + ",".join(cols)]
    values = df[cols].to_numpy(dtype="float64")
    for ts, row in zip(df.index, values):
        lines.append(ts.strftime("%Y-%m-%d") + "," + ",".join("" if v != v else repr(float(v)) for v in row))
    return "\n".join(lines) + "\n"


def rows_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(canonical_csv(df).encode("utf-8")).hexdigest()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Snapshot:
    meta: dict[str, Any]
    df: pd.DataFrame

    @property
    def id(self) -> str:
        return self.meta["id"]

    def column(self, name: str = "close") -> pd.Series:
        if name not in self.df.columns:
            raise MidasError("invalid_request", f"Snapshot {self.id} has no '{name}' column (has: {', '.join(self.df.columns)}).",
                             "Use one of the listed columns.")
        return self.df[name]


def provenance(meta: dict[str, Any]) -> dict[str, Any]:
    """The compact provenance block attached to every number that comes from a snapshot."""
    return {k: meta.get(k) for k in ("id", "provider", "symbol", "fetched_at", "req_start", "req_end", "actual_start", "actual_end",
                                      "frequency", "currency", "adjusted", "unit", "rows", "sha256", "terms", "delay")}


class SnapshotStore:
    def __init__(self, db: Database, directory: Path, clock: Callable[[], float] = time.time):
        self.db = db
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock

    # ------------------------------------------------------------------ write
    def save(self, result: FetchResult, provider: Provider, symbol: str, *, req_start: Optional[str], req_end: Optional[str],
             label: str = "") -> tuple[dict[str, Any], bool]:
        """Store a fetch. Identical bytes for the same provider/symbol/frequency reuse the existing snapshot."""
        df = result.df
        sha = rows_sha256(df)
        existing = self.db.one("SELECT id FROM snapshots WHERE provider=? AND provider_symbol=? AND sha256=? AND frequency=? AND currency=? "
                               "AND unit=? AND adjusted=?", (provider.id, result.provider_symbol, sha, result.frequency,
                                                             result.currency, result.unit, int(result.adjusted)))
        if existing:
            return self.meta(existing["id"]), False
        sid = "snp_" + uuid.uuid4().hex[:10]
        text = canonical_csv(df)
        write_text_atomic(self.dir / f"{sid}.csv", text)  # a frozen snapshot is never half written (its sha256 is its identity)
        now = self.clock()
        self.db.execute(
            "INSERT INTO snapshots(id, provider, symbol, provider_symbol, label, kind, fetched_at, req_start, req_end, actual_start, "
            "actual_end, frequency, periods_per_year, currency, adjusted, unit, rows, sha256, columns, terms, delay, extra, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, provider.id, symbol, result.provider_symbol, label or result.label or symbol, result.kind, now, req_start or "",
             req_end or "", df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d"), result.frequency,
             result.periods_per_year, result.currency, int(result.adjusted), result.unit, len(df), sha,
             json.dumps([c for c in OHLCV if c in df.columns]), provider.terms, provider.delay,
             json.dumps(result.extra), result.note))
        return self.meta(sid), True

    # ------------------------------------------------------------------- read
    @staticmethod
    def _meta(row) -> dict[str, Any]:
        return {
            "id": row["id"], "provider": row["provider"], "symbol": row["symbol"], "provider_symbol": row["provider_symbol"],
            "label": row["label"], "kind": row["kind"], "fetched_at": iso(row["fetched_at"]), "fetched_ts": row["fetched_at"],
            "req_start": row["req_start"], "req_end": row["req_end"], "actual_start": row["actual_start"],
            "actual_end": row["actual_end"], "frequency": row["frequency"], "periods_per_year": row["periods_per_year"],
            "currency": row["currency"], "adjusted": bool(row["adjusted"]), "unit": row["unit"], "rows": row["rows"],
            "sha256": row["sha256"], "columns": json.loads(row["columns"] or "[]"), "terms": row["terms"], "delay": row["delay"],
            "extra": json.loads(row["extra"] or "{}"), "note": row["note"],
        }

    def meta(self, snapshot_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
        if row is None:
            raise MidasError("not_found", f"Unknown snapshot '{snapshot_id}'.", "List snapshots with snapshots_list.")
        return self._meta(row)

    def load(self, snapshot_id: str, *, as_of: Optional[str] = None) -> Snapshot:
        """Rows + metadata, verified against the stored hash. ``as_of`` slices the rows (point-in-time)."""
        meta = self.meta(snapshot_id)
        path = self.dir / f"{snapshot_id}.csv"
        try:
            data = path.read_bytes()
        except OSError as error:
            raise MidasError("integrity_error", f"The data file of {snapshot_id} is missing.", "Fetch the series again.") from error
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise MidasError("integrity_error", f"Snapshot {snapshot_id} no longer matches its sha256: the file was modified.",
                             "Restore data/snapshots from a backup or fetch the series again (a new snapshot id).")
        import io

        df = pd.read_csv(io.BytesIO(data), index_col=0, parse_dates=True, float_precision="round_trip")
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        if as_of:
            df = df[df.index <= pd.Timestamp(as_of)]
        return Snapshot(meta, df)

    def list(self, *, provider: Optional[str] = None, symbol: Optional[str] = None, query: Optional[str] = None,
             limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        where, params = [], []
        if provider:
            where.append("provider = ?")
            params.append(provider.lower())
        if symbol:
            where.append("lower(symbol) = ?")
            params.append(symbol.lower())
        if query:
            where.append("(lower(symbol) LIKE ? OR lower(label) LIKE ? OR id = ?)")
            like = f"%{query.lower()}%"
            params.extend([like, like, query])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.one(f"SELECT COUNT(*) AS n FROM snapshots{clause}", params)["n"]
        rows = self.db.query(f"SELECT * FROM snapshots{clause} ORDER BY fetched_at DESC, rowid DESC LIMIT ? OFFSET ?", [*params, limit, offset])
        return [self._meta(r) for r in rows], total

    def latest_for_symbol(self, symbol: str, provider: Optional[str] = None) -> Optional[dict[str, Any]]:
        sql = "SELECT * FROM snapshots WHERE (lower(symbol) = ? OR lower(provider_symbol) = ?)"
        params: list[Any] = [symbol.lower(), symbol.lower()]
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        row = self.db.one(sql + " ORDER BY fetched_at DESC, rowid DESC LIMIT 1", params)
        return self._meta(row) if row else None

    def count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM snapshots")["n"]
