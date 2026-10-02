"""/api/status (the cheap /api/health comes from Hoard Link's health_router)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import services

router = APIRouter(prefix="/api")


@router.get("/status")
def status(request: Request):
    return services(request).status()
