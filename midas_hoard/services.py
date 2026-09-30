"""Wiring: database, providers, snapshot store, theses, lab, committee, portfolios and reports behind one object
that the API routers and the agent tools share."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pandas as pd

from . import SERVICE, __version__
from . import reports as reports_mod
from . import series as series_mod
from .committee import Committee, build_pack
from .config import Config
from .db import Database
from .errors import MidasError
from .hoard_link.config import LinkConfig
from .lab import Lab
from .portfolio import Portfolios
from .providers import HttpClient, Registry
from .providers.base import FetchResult
from .snapshots import Snapshot, SnapshotStore, provenance
from .theses import Theses

log = logging.getLogger("midas")
DISCLAIMER = reports_mod.DISCLAIMER


def write_token(config: Config) -> str:
    """The MCP token is persistent: created once, reused on every later start.

    Rotating it on each start would break a bridge (or a second, port-clashing instance would break the running one)
    the moment the file changed.
    """
    config.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        existing = config.token_path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if len(existing) >= 32:
        return existing
    token = secrets.token_hex(32)
    config.token_path.write_text(token, encoding="utf-8")
    try:
        config.token_path.chmod(0o600)
    except OSError:
        pass
    return token


def write_url(config: Config) -> None:
    try:
        config.url_path.write_text(f"http://127.0.0.1:{config.port}", encoding="utf-8")
    except OSError:
        pass


class Services:
    def __init__(self, config: Config, *, link: Any = None, http_transport: Optional[httpx.BaseTransport] = None,
                 clock_fn: Callable[[], float] = time.time, sleep_fn: Callable[[float], None] = time.sleep):
        self.config = config
        self.clock = clock_fn
        self.started_at = time.time()
        for d in (config.data_dir, config.snapshots_dir, config.runs_dir, config.reports_dir, config.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.token = write_token(config)
        write_url(config)
        self.db = Database(config.db_path)
        offline = config.offline or self.db.get_setting("offline", "0") == "1"
        self.http = HttpClient(config.cache_dir, timeout=config.http_timeout_s, ttl=config.cache_ttl_s, offline=offline,
                               transport=http_transport, sleep=sleep_fn, clock=clock_fn)
        self.providers = Registry(self.http, keys=self._provider_key, coingecko={"clock": clock_fn, "sleep": sleep_fn})
        self.store = SnapshotStore(self.db, config.snapshots_dir, clock_fn)
        if link is not None:
            self.link_sync = link  # test double, already "sync-shaped"
            self._link = None
        else:
            from .hoard_link.link import Link
            link_config = LinkConfig.load(config.backend_json_path if config.backend_json_path.is_file() else None, app="midas")
            self._link = Link(link_config)
            self.link_sync = self._link.sync
        self.theses = Theses(self.db, self.store, fetch=self._fetch_for_thesis, catalogue=self.catalogue_lookup, emit=self._emit, clock=clock_fn)
        self.lab = Lab(self.db, self.store, config.runs_dir, emit=self._emit, clock=clock_fn)
        self.committee = Committee(self.db, self.theses, self.store, self.link_sync, clock_fn)
        self.portfolios = Portfolios(self.db, self.store, clock_fn)
        self.seed_symbols()

    # ------------------------------------------------------------ secrets
    def _provider_key(self, provider_id: str) -> str:
        """API key for a keyed provider: the environment wins, then the write-only value saved from Settings."""
        env = os.environ.get(f"MIDAS_{provider_id.upper()}_KEY", "").strip()
        return env or (self.db.get_setting(f"secret.{provider_id}", "") or "").strip()

    def _keys_status(self) -> dict[str, Any]:
        """Never the value: only whether a key exists, where it comes from and its last four characters."""
        out = {}
        for p in self.providers.all():
            if not p.needs_key:
                continue
            key = self._provider_key(p.id)
            env = bool(os.environ.get(f"MIDAS_{p.id.upper()}_KEY", "").strip())
            out[p.id] = {"configured": bool(key), "last4": key[-4:] if len(key) >= 8 else ("" if not key else "****"),
                         "source": "env" if env else ("settings" if key else "")}
        return out

    # -------------------------------------------------------------- events
    def _emit(self, type_: str, data: dict[str, Any]) -> None:
        try:
            from .hoard_link import family
            family.emit(type_, data)
        except Exception:  # noqa: BLE001 — events are hints; the database is the truth
            pass

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.http.close()
        if self._link is not None:
            try:
                self.link_sync.close()
            except Exception:  # noqa: BLE001
                pass
        self.db.close()

    # ------------------------------------------------------------ catalogue
    def seed_symbols(self) -> int:
        path = Path(__file__).resolve().parent / "seeds.json"
        try:
            seeds = json.loads(path.read_text(encoding="utf-8")).get("symbols", [])
        except (OSError, ValueError):
            return 0
        n = 0
        with self.db.transaction() as conn:
            for s in seeds:
                cur = conn.execute("INSERT OR IGNORE INTO symbols(provider, symbol, name, kind, currency, unit, frequency, seed, extra) VALUES (?,?,?,?,?,?,?,1,?)",
                                   (s["provider"], s["symbol"], s.get("name", ""), s.get("kind", ""), s.get("currency", ""), s.get("unit", ""),
                                    s.get("frequency", "D"), json.dumps(s.get("extra", {}))))
                n += cur.rowcount
        return n

    def catalogue_lookup(self, symbol: str) -> Optional[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM symbols WHERE lower(symbol) = ? ORDER BY (last_seen_ts IS NULL), last_seen_ts DESC, seed DESC", (symbol.lower(),))
        return dict(rows[0]) if rows else None

    def _remember(self, provider: str, symbol: str, res: FetchResult) -> None:
        self.db.execute(
            "INSERT INTO symbols(provider, symbol, name, kind, currency, unit, frequency, seed, extra, last_seen_ts) VALUES (?,?,?,?,?,?,?,0,?,?) "
            "ON CONFLICT(provider, symbol) DO UPDATE SET last_seen_ts = excluded.last_seen_ts",
            (provider, symbol, res.label or symbol, res.kind, res.currency, res.unit, res.frequency, json.dumps(res.extra), self.clock()))

    def market_search(self, query: str, *, provider: Optional[str] = None, remote: bool = False, limit: int = 20) -> dict[str, Any]:
        q = query.strip().lower()
        where, params = ["(lower(symbol) LIKE ? OR lower(name) LIKE ? OR lower(kind) = ?)"], [f"%{q}%", f"%{q}%", q]
        if provider:
            where.append("provider = ?")
            params.append(provider.lower())
        rows = self.db.query(f"SELECT * FROM symbols WHERE {' AND '.join(where)} ORDER BY seed DESC, (last_seen_ts IS NULL), last_seen_ts DESC, symbol LIMIT ?", [*params, limit])
        found = [{"provider": r["provider"], "symbol": r["symbol"], "name": r["name"], "kind": r["kind"], "currency": r["currency"], "unit": r["unit"],
                  "frequency": r["frequency"], "seed": bool(r["seed"]), "snapshots": self.db.one("SELECT COUNT(*) AS n FROM snapshots WHERE provider=? AND symbol=?",
                                                                                               (r["provider"], r["symbol"]))["n"]} for r in rows]
        notes: list[str] = []
        if remote or provider == "fake":
            for p in self.providers.all():
                if provider and p.id != provider:
                    continue
                if p.id == "fake" or (remote and p.needs_network and not self.http.offline):
                    try:
                        for hit in p.search(query, limit=limit):
                            if not any(f["provider"] == hit["provider"] and f["symbol"] == hit["symbol"] for f in found):
                                found.append({**hit, "seed": False, "snapshots": 0, "remote": p.id != "fake"})
                    except MidasError as error:
                        notes.append(f"{p.id}: {error.message}")
        return {"query": query, "results": found[:limit], "count": len(found[:limit]), "notes": notes,
                "hint": "market_fetch(provider, symbol) stores a snapshot; symbol syntax: yahoo AAPL / ^GSPC / EURUSD=X, fred CPIAUCSL, ecb EXR/D.USD.EUR.SP00.A, coingecko bitcoin:eur."}

    # --------------------------------------------------------------- market
    def fetch_raw(self, provider: str, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d",
                  options: Optional[dict[str, Any]] = None, label: str = "") -> tuple[dict[str, Any], bool, FetchResult]:
        prov = self.providers.get(provider)
        ok, reason = prov.available()
        if not ok:
            raise MidasError("provider_unavailable", f"{prov.name} is not available: {reason}.", "Use another provider (see market_providers).", provider=prov.id)
        for name, value in (("start", start), ("end", end)):
            if value:
                try:
                    pd.Timestamp(value)
                except (ValueError, TypeError):
                    raise MidasError("invalid_request", f"'{name}' is not a date: {value!r}.", "Use YYYY-MM-DD.") from None
        if start and end and pd.Timestamp(start) > pd.Timestamp(end):
            raise MidasError("invalid_request", "start is after end.", "Swap them.")
        res = prov.fetch(symbol, start, end, interval or "d", **(options or {}))
        if res.df.empty:
            raise MidasError("no_data", f"{prov.id} returned no rows for '{symbol}'.", "Widen the range.")
        meta, created = self.store.save(res, prov, symbol, req_start=start, req_end=end, label=label)
        self._remember(prov.id, symbol, res)
        if created:
            self._emit("midas.snapshot.created", {"snapshot": meta["id"], "provider": prov.id, "symbol": symbol[:40], "rows": meta["rows"]})
        return meta, created, res

    def _fetch_for_thesis(self, provider, symbol, start, end, interval, options):
        meta, created, _ = self.fetch_raw(provider, symbol, start, end, interval, options)
        return meta, created

    def market_fetch(self, *, provider: str, symbol: str, start: Optional[str] = None, end: Optional[str] = None, interval: str = "d",
                     currency: Optional[str] = None, unit: Optional[str] = None, label: str = "", options: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        opts = dict(options or {})
        if currency:
            opts["currency"] = currency
        if unit:
            opts["unit"] = unit
        meta, created, res = self.fetch_raw(provider, symbol, start, end, interval, opts, label)
        snap = self.store.load(meta["id"])
        head = snap.df.head(3)
        tail = snap.df.tail(3)
        preview = [{"date": ts.strftime("%Y-%m-%d"), **{k: (None if pd.isna(v) else v) for k, v in row.items()}} for ts, row in pd.concat([head, tail]).drop_duplicates().iterrows()]
        warnings = []
        if not meta["currency"] and meta["kind"] == "ohlcv":
            warnings.append("No currency is declared for this series: it will be treated as unit-less (pass currency= to set it).")
        return {"snapshot": provenance(meta), "created": created, "reused_existing": not created, "from_http_cache": res.cached, "note": meta["note"],
                "kind": meta["kind"], "columns": meta["columns"], "preview": preview, "warnings": warnings,
                "next": f"market_series(snapshot_id='{meta['id']}') to look at it; thesis / strategy specs cite this snapshot id."}

    def snapshots_list(self, **kwargs) -> dict[str, Any]:
        items, total = self.store.list(**kwargs)
        return {"snapshots": [provenance(m) | {"label": m["label"], "kind": m["kind"], "columns": m["columns"], "note": m["note"]} for m in items], "total": total}

    def _fx_arg(self, fx: Any, snapshot_ids: list[str]) -> dict[str, Snapshot]:
        if not fx:
            return {}
        if isinstance(fx, str):
            snap = self.store.load(fx)
            return {sid: snap for sid in snapshot_ids}
        return {sid: self.store.load(v) for sid, v in fx.items()}

    def market_series(self, snapshot_id: str, *, fx: Any = None, **view) -> dict[str, Any]:
        snap = self.store.load(snapshot_id)
        rate = None
        if fx:
            rate = self.store.load(fx if isinstance(fx, str) else next(iter(fx.values())))
        return series_mod.series_view(snap, fx=rate, **view)

    def market_compare(self, snapshot_ids: list[str], *, fx: Any = None, **kwargs) -> dict[str, Any]:
        snaps = [self.store.load(s) for s in snapshot_ids]
        return series_mod.compare(snaps, fx=self._fx_arg(fx, snapshot_ids), **kwargs)

    # -------------------------------------------------------------- reports
    def report_export(self, kind: str, ident: str, fmt: str = "md", save: bool = True) -> dict[str, Any]:
        if kind == "thesis":
            t = self.theses.get(ident)
            built = build_pack(self.theses, self.store, ident)
            committee = self.committee.latest(ident)
            ids = {a["snapshot"]["id"] for a in built["pack"]["assets"]} | {e["ref"] for e in t["evidence"] if e["kind"] == "snapshot_metric"}
            if t.get("last_check"):
                ids |= set(t["last_check"]["snapshots"])
            metas = [provenance(self.store.meta(i)) for i in sorted(ids)]
            markdown = reports_mod.thesis_markdown(t, pack=built["pack"], committee=committee, snapshots=metas)
            data = {"thesis": t, "evidence_pack": built["pack"], "committee": committee, "snapshots": metas, "disclaimer": DISCLAIMER}
        elif kind == "run":
            run = self.lab.run_get(ident, equity_limit=10_000, trades_limit=10_000)
            if run["status"] != "ok":
                raise MidasError("invalid_request", f"Run {ident} has status {run['status']}: {run.get('error') or 'no report'}.", "Only finished backtests have a report.")
            report = self.lab.runs_dir / ident / "report.md"
            markdown = report.read_text(encoding="utf-8") if report.is_file() else ""
            data = run
        elif kind == "portfolio":
            analysis = self.portfolios.analyze(ident)
            markdown = reports_mod.portfolio_markdown(analysis)
            data = analysis
        else:
            raise MidasError("invalid_request", f"Unknown report kind '{kind}'.", "Use thesis, run or portfolio.")
        path = None
        if save:
            self.config.reports_dir.mkdir(parents=True, exist_ok=True)
            base = self.config.reports_dir / f"{kind}-{ident}"
            if fmt == "json":
                path = base.with_suffix(".json")
                path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            else:
                path = base.with_suffix(".md")
                path.write_text(markdown, encoding="utf-8")
        return {"kind": kind, "id": ident, "format": fmt, "markdown": markdown, "json": data if fmt == "json" else None, "path": str(path) if path else None,
                "generated_at": reports_mod.now_iso(), "disclaimer": DISCLAIMER}

    # ------------------------------------------------------------- settings
    def get_settings(self) -> dict[str, Any]:
        backend = {}
        if self.config.backend_json_path.is_file():
            try:
                backend = json.loads(self.config.backend_json_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                backend = {}
        return {"backend": backend, "language": self.db.get_setting("language", "es"), "offline": self.http.offline,
                "cache": self.http.cache_stats(), "data_dir": str(self.config.data_dir),
                "keys": self._keys_status()}

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        if patch.get("backend") is not None:
            self.config.backend_json_path.write_text(json.dumps(patch["backend"], indent=2), encoding="utf-8")
        if patch.get("language") in ("en", "es"):
            self.db.set_setting("language", patch["language"])
        if "offline" in patch:
            self.http.offline = bool(patch["offline"])
            self.db.set_setting("offline", "1" if patch["offline"] else "0")
        for pid, value in (patch.get("keys") or {}).items():
            prov = self.providers.get(pid)
            if not prov.needs_key:
                raise MidasError("invalid_request", f"{prov.id} does not use an API key.", "Only keyed providers accept one.")
            value = (value or "").strip()
            if value and (len(value) > 200 or any(c.isspace() for c in value)):
                raise MidasError("invalid_request", "That does not look like an API key.", "Paste the key only, without spaces.")
            self.db.set_setting(f"secret.{prov.id}", value)
        if patch.get("clear_cache"):
            self.http.clear_cache()
        return self.get_settings()

    # --------------------------------------------------------------- status
    def counts(self) -> dict[str, int]:
        q = lambda t: self.db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]  # noqa: E731 — fixed table names
        return {"snapshots": q("snapshots"), "symbols": q("symbols"), "theses": q("theses"), "strategies": q("strategies"),
                "experiments": q("experiments"), "portfolios": q("portfolios")}

    def status(self) -> dict[str, Any]:
        """The full picture, including the (network-probing, ~seconds) model resolution."""
        try:
            link_status = self.link_sync.status()
        except Exception as error:  # noqa: BLE001
            link_status = {"error": str(error)}

        def size(path: Path) -> int:
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else 0

        return {"service": SERVICE, "version": __version__, "data_dir": str(self.config.data_dir), "schema_version": self.db.version(),
                "started_at": self.started_at, "now": self.clock(), "counts": self.counts(), "theses_by_status": self.theses.counts(),
                "providers": self.providers.describe(), "offline": self.http.offline, "cache": self.http.cache_stats(),
                "disk": {"snapshots_bytes": size(self.config.snapshots_dir), "runs_bytes": size(self.config.runs_dir)},
                "models": link_status, "disclaimer": DISCLAIMER}
