"""Provider interface, the polite HTTP client with its on-disk cache, and frame normalisation."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pandas as pd

from ..errors import MidasError

USER_AGENT = "MidasHoard/0.1 (local research tool; single user; +https://github.com/Luissalet)"
OHLCV = ["open", "high", "low", "close", "volume"]
PPY = {"D": 252.0, "W": 52.0, "M": 12.0, "Q": 4.0, "A": 1.0}


def infer_frequency(index: pd.DatetimeIndex) -> str:
    """D / W / M / Q / A from the typical gap between observations (lower quartile, so a few missing points do not change it)."""
    if len(index) < 3:
        return "D"
    gaps = pd.Series(index[1:] - index[:-1]).dt.days
    median = float(gaps.quantile(0.25))
    if median <= 4:
        return "D"
    if median <= 10:
        return "W"
    if median <= 40:
        return "M"
    if median <= 120:
        return "Q"
    return "A"


def normalise(df: pd.DataFrame, columns: Optional[list[str]] = None) -> pd.DataFrame:
    """Sorted, unique, tz-naive daily DatetimeIndex; float64 columns; rows without a close dropped."""
    frame = df.copy()
    index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    frame.index = index.normalize()
    frame = frame[~frame.index.isna()]
    keep = [c for c in (columns or OHLCV) if c in frame.columns]
    frame = frame[keep].apply(pd.to_numeric, errors="coerce").astype("float64")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if "close" in frame.columns:
        frame = frame[frame["close"].notna()]
    frame.index.name = "date"
    return frame


def slice_range(df: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    out = df
    if start:
        out = out[out.index >= pd.Timestamp(start)]
    if end:
        out = out[out.index <= pd.Timestamp(end)]
    return out


@dataclass
class FetchResult:
    """What a provider hands back: normalised rows plus everything the snapshot must record."""

    df: pd.DataFrame
    provider_symbol: str
    currency: str = ""
    unit: str = ""
    frequency: str = "D"
    adjusted: bool = False
    periods_per_year: float = 252.0
    kind: str = "ohlcv"  # ohlcv | series
    label: str = ""
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    cached: bool = False


@dataclass
class HttpResponse:
    status: int
    text: str
    cached: bool
    fetched_at: float
    url: str
    content_type: str = ""

    @property
    def status_code(self) -> int:
        return self.status


class HttpClient:
    """httpx with timeouts, a polite User-Agent, a small on-disk response cache and clear errors.

    Only successful responses are cached. ``offline`` answers from the cache or fails with
    ``provider_unavailable`` — tests and airplane mode never touch the network.
    """

    def __init__(self, cache_dir: Path, *, timeout: float = 20.0, ttl: int = 6 * 3600, offline: bool = False,
                 transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        self.cache_dir = Path(cache_dir) / "http"
        self.timeout = timeout
        self.ttl = ttl
        self.offline = offline
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self._client: Optional[httpx.Client] = None
        self.hits = 0
        self.misses = 0

    # -- cache ------------------------------------------------------------
    def _key(self, url: str, params: Optional[dict[str, Any]]) -> str:
        canon = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params or {}))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _read_cache(self, key: str, ttl: int, allow_stale: bool) -> Optional[dict[str, Any]]:
        path = self._path(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if allow_stale or self.clock() - float(entry.get("fetched_at", 0)) <= ttl:
            return entry
        return None

    def _write_cache(self, key: str, url: str, status: int, text: str, fetched_at: float, content_type: str = "") -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "status": status, "text": text, "fetched_at": fetched_at, "content_type": content_type}), encoding="utf-8")
        except OSError:
            pass

    def cache_stats(self) -> dict[str, Any]:
        files = list(self.cache_dir.glob("*/*.json")) if self.cache_dir.is_dir() else []
        size = 0
        for f in files:
            try:
                size += f.stat().st_size
            except OSError:
                pass
        return {"entries": len(files), "bytes": size, "ttl_s": self.ttl, "hits": self.hits, "misses": self.misses,
                "dir": str(self.cache_dir)}

    def clear_cache(self) -> int:
        removed = 0
        for f in list(self.cache_dir.glob("*/*.json")) if self.cache_dir.is_dir() else []:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    # -- requests ---------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=True, transport=self.transport,
                                        headers={"User-Agent": USER_AGENT})
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def get(self, url: str, params: Optional[dict[str, Any]] = None, *, ttl: Optional[int] = None, provider: str = "",
            use_cache: bool = True, headers: Optional[dict[str, str]] = None) -> HttpResponse:
        ttl = self.ttl if ttl is None else ttl
        key = self._key(url, params)
        if use_cache and ttl > 0:
            entry = self._read_cache(key, ttl, allow_stale=False)
            if entry:
                self.hits += 1
                return HttpResponse(int(entry["status"]), entry["text"], True, float(entry["fetched_at"]), url, entry.get("content_type", ""))
        if self.offline:
            entry = self._read_cache(key, ttl, allow_stale=True)
            if entry:
                self.hits += 1
                return HttpResponse(int(entry["status"]), entry["text"], True, float(entry["fetched_at"]), url, entry.get("content_type", ""))
            raise MidasError("provider_unavailable", f"{provider or 'provider'}: offline mode, nothing cached for this request.",
                             "Unset MIDAS_OFFLINE to allow network access, or use the csv/fake providers.", provider=provider)
        self.misses += 1
        last_error = ""
        for attempt in range(2):
            try:
                response = self._http().get(url, params=params, headers=headers)
            except httpx.HTTPError as error:
                last_error = f"{type(error).__name__}: {error}"
                if attempt == 0:
                    self.sleep(0.6)
                    continue
                stale = self._read_cache(key, ttl, allow_stale=True)
                if stale:
                    return HttpResponse(int(stale["status"]), stale["text"], True, float(stale["fetched_at"]), url, stale.get("content_type", ""))
                raise MidasError("provider_unavailable", f"{provider or 'provider'} could not be reached ({last_error}).",
                                 "Check the connection; cached responses are used when present.", provider=provider) from error
            if response.status_code == 429:
                retry = response.headers.get("retry-after", "")
                raise MidasError("rate_limited", f"{provider or 'provider'} rate limit reached.",
                                 f"Wait {retry + ' s' if retry else 'a minute'} and retry; cached data is reused for {self.ttl // 3600} h.",
                                 provider=provider, retry_after=retry or None)
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                if attempt == 0:
                    self.sleep(0.6)
                    continue
                stale = self._read_cache(key, ttl, allow_stale=True)
                if stale:
                    return HttpResponse(int(stale["status"]), stale["text"], True, float(stale["fetched_at"]), url, stale.get("content_type", ""))
                raise MidasError("provider_unavailable", f"{provider or 'provider'} answered {last_error}.", "Try again later.",
                                 provider=provider)
            now = self.clock()
            ctype = response.headers.get("content-type", "")
            if response.status_code == 200 and "text/html" not in ctype.lower():
                self._write_cache(key, url, 200, response.text, now, ctype)  # challenge pages are never cached
            return HttpResponse(response.status_code, response.text, False, now, url, ctype)
        raise MidasError("provider_unavailable", f"{provider or 'provider'}: {last_error}", provider=provider)  # pragma: no cover


class Provider:
    """Interface every data provider implements."""

    id = "base"
    name = "Base"
    terms = ""
    delay = ""
    license = ""
    unofficial = False
    needs_network = True
    intervals = ("d",)
    needs_key = False
    blocked = ""  # non-empty: why the provider currently cannot be used from a script

    def __init__(self, http: HttpClient):
        self.http = http
        self.key_source: Optional[Callable[[str], str]] = None

    def key(self) -> str:
        return (self.key_source(self.id) if self.key_source else "").strip()

    def available(self) -> tuple[bool, str]:
        return True, ""

    def describe(self) -> dict[str, Any]:
        ok, reason = self.available()
        status = "ok"
        if self.blocked:
            status, reason = "blocked", self.blocked
        elif self.needs_key and not ok:
            status = "needs_key"
        elif not ok:
            status = "unavailable"
        return {"status": status, "needs_key": self.needs_key, "configured": bool(self.key()) if self.needs_key else None, "id": self.id, "name": self.name, "terms": self.terms, "delay": self.delay, "license": self.license,
                "unofficial": self.unofficial, "available": ok, "reason": reason, "needs_network": self.needs_network,
                "intervals": list(self.intervals)}

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Remote search where the provider offers one; the local catalogue is searched by the caller."""
        return []

    def fetch(self, symbol: str, start: Optional[str], end: Optional[str], interval: str = "d", **options: Any) -> FetchResult:
        raise NotImplementedError
