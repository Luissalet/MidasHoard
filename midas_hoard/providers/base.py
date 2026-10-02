"""Provider interface, the polite HTTP client with its on-disk cache, and frame normalisation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pandas as pd

from ..errors import MidasError
from ..hoard_link.web.fetch import ApiError, ApiResponse, JsonApiClient

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


HttpResponse = ApiResponse  # what ``HttpClient.get`` returns: ``status``, ``text``, ``cached``, ``fetched_at``, ``url``, ``content_type``, ``headers``


def _tests_resolver(host: str, port: int) -> list[str]:
    """With an injected transport nothing touches the network, so the host does not need to resolve (tests)."""
    return ["93.184.216.34"]


class HttpClient(JsonApiClient):
    """The shared Hoard Link JSON API client (identifying User-Agent, on-disk cache, stale-if-error, retry on network errors and
    5xx, ``Retry-After``, offline switch) with Midas's errors: every failure is a :class:`MidasError` that names the provider.

    Only successful, non-HTML responses are cached. ``offline`` answers from the cache or fails with ``provider_unavailable``
    and can be flipped while the app runs (the Settings switch)."""

    def __init__(self, cache_dir: Path, *, timeout: float = 20.0, ttl: int = 6 * 3600, offline: bool = False,
                 transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, resolver: Optional[Callable[[str, int], Any]] = None):
        self.clock = clock
        self.sleep = sleep
        super().__init__(user_agent=USER_AGENT, ttl=ttl, offline=offline, retries=1, cache_dir=cache_dir, timeout_s=timeout,
                         transport=transport, clock=clock, sleep=sleep, resolver=resolver or (_tests_resolver if transport is not None else None))

    @property
    def offline(self) -> bool:
        return self._offline

    @offline.setter
    def offline(self, value: bool) -> None:
        self._offline = bool(value)
        fetcher = self.__dict__.get("_fetcher")
        if fetcher is not None:
            fetcher.offline = self._offline

    def get(self, url: str, params: Optional[dict[str, Any]] = None, *, ttl: Optional[int] = None, provider: str = "",
            use_cache: bool = True, headers: Optional[dict[str, str]] = None) -> ApiResponse:
        who = provider or "provider"
        try:
            return super().get(url, params, ttl=ttl, use_cache=use_cache, headers=headers, label=who)
        except ApiError as error:
            raise self._as_midas(error, who, provider) from error

    def _as_midas(self, error: ApiError, who: str, provider: str) -> MidasError:
        if error.kind == "offline":
            return MidasError("provider_unavailable", f"{who}: offline mode, nothing cached for this request.",
                              "Unset MIDAS_OFFLINE to allow network access, or use the csv/fake providers.", provider=provider)
        if error.kind == "rate_limited":
            retry = error.retry_after or ""
            return MidasError("rate_limited", f"{who} rate limit reached.",
                              f"Wait {retry + ' s' if retry else 'a minute'} and retry; cached data is reused for {int(self.ttl) // 3600} h.",
                              provider=provider, retry_after=retry or None)
        if error.kind == "unreachable":
            return MidasError("provider_unavailable", str(error), "Check the connection; cached responses are used when present.", provider=provider)
        if error.kind == "server_error":
            return MidasError("provider_unavailable", str(error), "Try again later.", provider=provider)
        return MidasError("provider_unavailable", str(error), "The provider refused the request (a browser check or a block); try again later.",
                          provider=provider)


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
