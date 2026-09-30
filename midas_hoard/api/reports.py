"""/api/reports: Markdown / JSON exports of a thesis, a run or a portfolio."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .deps import tool

router = APIRouter(prefix="/api")


@router.post("/reports")
def report(request: Request, body: dict[str, Any]):
    return tool(request, "report_export", body)
