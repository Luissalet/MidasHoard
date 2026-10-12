"""/api/portfolios/*."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request

from .deps import clean, services, tool

router = APIRouter(prefix="/api")


@router.get("/portfolios")
def portfolios(request: Request):
    return tool(request, "portfolio_analyze")


@router.get("/portfolios/{name}")
def portfolio_get(request: Request, name: str):
    return services(request).portfolios.get(name)


@router.put("/portfolios/{name}")
def portfolio_set(request: Request, name: str, body: dict[str, Any]):
    return tool(request, "portfolio_set", {**body, "name": name, "action": "set"})


@router.patch("/portfolios/{name}")
def portfolio_patch(request: Request, name: str, body: dict[str, Any]):
    """Merge only the given currency / upsert / remove into the portfolio (no full replacement)."""
    return tool(request, "portfolio_set", {**clean(body), "name": name, "action": "patch"})


@router.delete("/portfolios/{name}")
def portfolio_delete(request: Request, name: str):
    return tool(request, "portfolio_set", {"name": name, "action": "delete", "confirm": True})


@router.post("/portfolios/{name}/analyze")
def portfolio_analyze(request: Request, name: str, body: Optional[dict[str, Any]] = None):
    return tool(request, "portfolio_analyze", {**clean(body or {}), "name": name})
