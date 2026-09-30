"""/api/market/* and /api/snapshots: providers, search, fetch, series, compare."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from .deps import clean, tool

router = APIRouter(prefix="/api")


@router.get("/market/providers")
def providers(request: Request):
    return tool(request, "market_providers")


@router.get("/market/search")
def search(request: Request, q: str = Query(..., min_length=1), provider: Optional[str] = None, remote: bool = False, limit: int = 20):
    return tool(request, "market_search", clean({"query": q, "provider": provider, "remote": remote, "limit": limit}))


@router.post("/market/fetch")
def fetch(request: Request, body: dict[str, Any]):
    return tool(request, "market_fetch", body)


@router.get("/snapshots")
def snapshots(request: Request, provider: Optional[str] = None, symbol: Optional[str] = None, query: Optional[str] = None,
              limit: int = 30, cursor: Optional[int] = None):
    return tool(request, "snapshots_list", clean({"provider": provider, "symbol": symbol, "query": query, "limit": limit, "cursor": cursor}))


@router.post("/market/series")
def series(request: Request, body: dict[str, Any]):
    return tool(request, "market_series", body)


@router.post("/market/compare")
def compare(request: Request, body: dict[str, Any]):
    return tool(request, "market_compare", body)
