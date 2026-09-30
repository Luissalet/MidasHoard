"""/api/settings."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .deps import services

router = APIRouter(prefix="/api")


class SettingsBody(BaseModel):
    backend: Optional[dict[str, Any]] = None
    language: Optional[str] = None
    offline: Optional[bool] = None
    clear_cache: Optional[bool] = None


@router.get("/settings")
def settings_get(request: Request):
    return services(request).get_settings()


@router.put("/settings")
def settings_put(request: Request, body: SettingsBody):
    return services(request).update_settings(body.model_dump(exclude_none=True))
