from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..consolidation_worker import ConsolidationWorker
from ..deps import get_consolidation_worker, get_ledger_db, get_patch_planner
from ..ledger import LedgerDB
from ..patch_planner import PatchPlanner
from ..schemas import (
    ConsolidationPlanRequest,
    ConsolidationRunRequest,
    ConsolidationRunResult,
    PatchApplyRequest,
    PatchApplyResult,
    PatchPlan,
    PatchRejectRequest,
    RollbackResult,
)

consolidation_router = APIRouter(prefix="/api/consolidation", tags=["consolidation"])


@consolidation_router.post("/run", response_model=ConsolidationRunResult)
def run_consolidation(
    payload: ConsolidationRunRequest,
    worker: ConsolidationWorker = Depends(get_consolidation_worker),
) -> ConsolidationRunResult:
    return worker.run(
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
        max_auto_apply=payload.max_auto_apply,
        force_reextract=payload.force_reextract,
    )


@consolidation_router.post("/rollback/{plan_id}", response_model=RollbackResult)
def rollback_consolidation_plan(
    plan_id: str,
    worker: ConsolidationWorker = Depends(get_consolidation_worker),
) -> RollbackResult:
    return worker.rollback(plan_id)


@consolidation_router.get("/status")
def consolidation_status(
    worker: ConsolidationWorker = Depends(get_consolidation_worker),
) -> dict:
    return worker.status()


@consolidation_router.post("/plan", response_model=list[PatchPlan])
def plan_consolidation(
    payload: ConsolidationPlanRequest,
    planner: PatchPlanner = Depends(get_patch_planner),
) -> list[PatchPlan]:
    return planner.plan_batch(batch_id=payload.batch_id, candidate_ids=payload.candidate_ids)


@consolidation_router.get("/plans")
def list_patch_plans(
    status: str | None = Query(default=None),
    target_page_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> dict:
    plans = ledger_db.list_patch_plans(status=status, target_page_id=target_page_id, limit=limit)
    return {"count": len(plans), "plans": plans}


@consolidation_router.get("/plans/{plan_id}")
def get_patch_plan(
    plan_id: str,
    planner: PatchPlanner = Depends(get_patch_planner),
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> dict:
    plan = ledger_db.get_patch_plan(plan_id)
    if plan is None:
        raise ValueError(f"Patch plan {plan_id} not found")
    return {"plan": plan, "preview": planner.preview_patch(plan_id)}


@consolidation_router.post("/apply/{plan_id}", response_model=PatchApplyResult)
def apply_patch_plan(
    plan_id: str,
    payload: PatchApplyRequest,
    planner: PatchPlanner = Depends(get_patch_planner),
) -> PatchApplyResult:
    return planner.apply_plan(plan_id, force=payload.force)


@consolidation_router.post("/reject/{plan_id}")
def reject_patch_plan(
    plan_id: str,
    payload: PatchRejectRequest,
    planner: PatchPlanner = Depends(get_patch_planner),
) -> dict:
    planner.reject_plan(plan_id, reason=payload.reason)
    return {"plan_id": plan_id, "status": "rejected", "reason": payload.reason}
