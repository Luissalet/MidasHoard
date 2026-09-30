"""/api/theses/* and /api/rules/validate."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request

from .. import rules
from .deps import clean, tool

router = APIRouter(prefix="/api")


@router.get("/theses")
def theses_list(request: Request, status: Optional[str] = None, query: Optional[str] = None, limit: int = 30, cursor: Optional[int] = None):
    return tool(request, "thesis_list", clean({"status": status, "query": query, "limit": limit, "cursor": cursor}))


@router.post("/theses")
def thesis_create(request: Request, body: dict[str, Any]):
    return tool(request, "thesis_create", body)


@router.get("/theses/{thesis_id}")
def thesis_get(request: Request, thesis_id: str, history: bool = True):
    return tool(request, "thesis_get", {"id": thesis_id, "history": history})


@router.patch("/theses/{thesis_id}")
def thesis_update(request: Request, thesis_id: str, body: dict[str, Any]):
    return tool(request, "thesis_update", {**body, "id": thesis_id, "action": "update"})


@router.delete("/theses/{thesis_id}")
def thesis_delete(request: Request, thesis_id: str):
    return tool(request, "thesis_update", {"id": thesis_id, "action": "delete", "confirm": True})


@router.post("/theses/{thesis_id}/evidence")
def evidence_add(request: Request, thesis_id: str, body: dict[str, Any]):
    return tool(request, "thesis_evidence_add", {**body, "thesis_id": thesis_id, "action": "add"})


@router.delete("/theses/{thesis_id}/evidence/{evidence_id}")
def evidence_delete(request: Request, thesis_id: str, evidence_id: str):
    return tool(request, "thesis_evidence_add", {"thesis_id": thesis_id, "action": "delete", "evidence_id": evidence_id, "confirm": True})


@router.post("/theses/{thesis_id}/check")
def thesis_check(request: Request, thesis_id: str, body: Optional[dict[str, Any]] = None):
    return tool(request, "thesis_check", {**(body or {}), "id": thesis_id})


@router.post("/theses/{thesis_id}/committee")
def thesis_committee(request: Request, thesis_id: str, body: Optional[dict[str, Any]] = None):
    return tool(request, "committee_run", {**(body or {}), "thesis_id": thesis_id})


@router.post("/rules/validate")
def rule_validate(body: dict[str, Any]):
    return rules.validate(str(body.get("rule", body.get("expr", ""))))
