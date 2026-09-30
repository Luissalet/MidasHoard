"""Data providers: one interface, several free sources, every failure explained."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..errors import MidasError
from .base import HttpClient, FetchResult, Provider
from .alphavantage import AlphaVantageProvider
from .coingecko import CoinGeckoProvider
from .csvimport import CsvProvider
from .ecb import EcbProvider
from .fake import FakeProvider
from .fred import FredProvider
from .stooq import StooqProvider
from .tiingo import TiingoProvider
from .yahoo import YahooProvider

PROVIDER_CLASSES = [YahooProvider, FredProvider, EcbProvider, CoinGeckoProvider, AlphaVantageProvider, TiingoProvider, StooqProvider,
                    CsvProvider, FakeProvider]


class Registry:
    """The providers of one running app, sharing one HTTP client (and its cache)."""

    def __init__(self, http: HttpClient, keys: Optional[Callable[[str], str]] = None, **overrides: Any):
        self.http = http
        self._providers: dict[str, Provider] = {}
        for cls in PROVIDER_CLASSES:
            provider = cls(http, **overrides.get(cls.id, {})) if overrides.get(cls.id) else cls(http)
            provider.key_source = keys
            self._providers[provider.id] = provider

    def get(self, provider_id: str) -> Provider:
        provider = self._providers.get(provider_id.lower())
        if provider is None:
            raise MidasError("not_found", f"Unknown provider '{provider_id}'.", f"Use one of: {', '.join(self._providers)}.")
        return provider

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def describe(self) -> list[dict[str, Any]]:
        return [p.describe() for p in self.all()]


__all__ = ["Registry", "Provider", "FetchResult", "HttpClient", "PROVIDER_CLASSES"]
