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


@router.patch("/strategies/{ident}")
def strategy_patch(request: Request, ident: str, body: dict[str, Any]):
    """Merge only ``spec_patch`` (and optionally ``note``) into a saved strategy."""
    return tool(request, "strategy_save", {"id": ident, "spec_patch": body.get("spec_patch"), "note": body.get("note") or ""})


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


@router.get("/data-health")
def data_health(request: Request, snapshot_ids: Optional[str] = None, run_id: Optional[str] = None, strategy: Optional[str] = None,
                start: Optional[str] = None, end: Optional[str] = None):
    """Data health of snapshots (comma-separated ids), of a run's data or of a saved strategy's universe."""
    ids = [x.strip() for x in snapshot_ids.split(",") if x.strip()] if snapshot_ids else None
    return tool(request, "data_health", clean({"snapshot_ids": ids, "run_id": run_id, "strategy": strategy, "start": start, "end": end}))


@router.get("/duplicates")
def duplicates(request: Request, run_id: Optional[str] = None, strategy: Optional[str] = None, min_level: str = "medium", limit: int = 20):
    return tool(request, "experiments_duplicates", clean({"run_id": run_id, "strategy": strategy, "min_level": min_level, "limit": limit}))


@router.post("/duplicates")
def duplicates_for_spec(request: Request, body: dict[str, Any]):
    """Duplicate risk of a spec that has not been run yet."""
    return tool(request, "experiments_duplicates", {"spec": body.get("spec", body)})


@router.get("/backtests/{run_id}/report")
def backtest_report(request: Request, run_id: str, format: str = "json"):
    """The lab report contract of a run: ``json`` (sections, tagged claims, decision), ``md`` or ``html`` (text in ``markdown``)."""
    res = tool(request, "report_export", {"kind": "run", "id": run_id, "format": "json" if format == "json" else format, "save": False, "page_chars": 16000})
    if format == "json":
        return res["json"]["report"]
    return {"run_id": run_id, "format": format, "text": res["markdown"], "total_chars": res.get("total_chars")}
