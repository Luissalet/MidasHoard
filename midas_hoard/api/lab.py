"""/api/lab/*: strategies, backtests, validation, experiments."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request

from .deps import clean, services, tool

router = APIRouter(prefix="/api/lab")


@router.get("/example")
def example():
    from ..strategy import EXAMPLE_SPEC
    return {"spec": EXAMPLE_SPEC}


@router.post("/validate")
def validate(request: Request, body: dict[str, Any]):
    return tool(request, "strategy_validate", {"spec": body.get("spec", body)})


@router.get("/strategies")
def strategies(request: Request):
    return tool(request, "strategies_list")


@router.get("/strategies/{ident}")
def strategy_get(request: Request, ident: str):
    return tool(request, "strategies_list", {"id": ident})


@router.post("/strategies")
def strategy_save(request: Request, body: dict[str, Any]):
    return tool(request, "strategy_save", {"action": "save", "spec": body.get("spec"), "note": body.get("note", "")})


@router.delete("/strategies/{ident}")
def strategy_delete(request: Request, ident: str):
    return tool(request, "strategy_save", {"action": "delete", "id": ident, "confirm": True})


@router.post("/backtests")
def backtest_run(request: Request, body: dict[str, Any]):
    return tool(request, "backtest_run", body)


@router.post("/backtests/{run_id}/validate")
def backtest_validate(request: Request, run_id: str, body: Optional[dict[str, Any]] = None):
    return tool(request, "backtest_validate", {**(body or {}), "run_id": run_id})


@router.get("/experiments")
def experiments(request: Request, family: Optional[str] = None, kind: Optional[str] = None, status: Optional[str] = None,
                limit: int = 30, cursor: Optional[int] = None):
    return tool(request, "experiments_list", clean({"family": family, "kind": kind, "status": status, "limit": limit, "cursor": cursor}))


@router.get("/experiments/{run_id}")
def experiment_get(request: Request, run_id: str, cursor: Optional[int] = None):
    return tool(request, "experiments_list", clean({"run_id": run_id, "cursor": cursor}))
