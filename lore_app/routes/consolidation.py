from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import get_consolidation_worker, get_ledger_db, get_patch_planner
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

if TYPE_CHECKING:
    from ..consolidation_worker import ConsolidationWorker
    from ..ledger import LedgerDB
    from ..patch_planner import PatchPlanner

consolidation_router = APIRouter(prefix="/api/consolidation", tags=["consolidation"])


def _plan_http_error(exc: ValueError) -> HTTPException:
    """Map planner/ledger ValueErrors to 404 (missing) or 409 (conflict) instead of 500."""
    detail = str(exc)
    code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_409_CONFLICT
    return HTTPException(status_code=code, detail=detail)


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
    try:
        return worker.rollback(plan_id)
    except ValueError as exc:
        raise _plan_http_error(exc) from exc


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Patch plan {plan_id} not found")
    try:
        preview = planner.preview_patch(plan_id)
    except ValueError as exc:
        raise _plan_http_error(exc) from exc
    return {"plan": plan, "preview": preview}


@consolidation_router.post("/apply/{plan_id}", response_model=PatchApplyResult)
def apply_patch_plan(
    plan_id: str,
    payload: PatchApplyRequest,
    planner: PatchPlanner = Depends(get_patch_planner),
) -> PatchApplyResult:
    try:
        return planner.apply_plan(plan_id, force=payload.force)
    except ValueError as exc:
        raise _plan_http_error(exc) from exc


@consolidation_router.post("/reject/{plan_id}")
def reject_patch_plan(
    plan_id: str,
    payload: PatchRejectRequest,
    planner: PatchPlanner = Depends(get_patch_planner),
) -> dict:
    try:
        planner.reject_plan(plan_id, reason=payload.reason)
    except ValueError as exc:
        raise _plan_http_error(exc) from exc
    return {"plan_id": plan_id, "status": "rejected", "reason": payload.reason}


@consolidation_router.get("/blocked")
def get_blocked_claims(ledger: LedgerDB = Depends(get_ledger_db)):
    blocked: list[dict] = []
    try:
        candidates = ledger.get_candidates(candidate_type="claim", limit=500)
    except Exception:
        return {"blocked": [], "total": 0}

    for candidate in candidates:
        if candidate.get("status") != "candidate":
            continue
        epistemic = candidate.get("epistemic_status")
        if epistemic == "assumption":
            blocked.append(
                {
                    "claim_id": candidate.get("candidate_id"),
                    "reason": "Assumption-labeled claims require human review before auto-apply.",
                    "epistemic_status": epistemic,
                }
            )
        elif epistemic == "inferred" and not candidate.get("trace_id"):
            blocked.append(
                {
                    "claim_id": candidate.get("candidate_id"),
                    "reason": "Inferred claims without a supporting trace require review.",
                    "epistemic_status": epistemic,
                }
            )

    return {"blocked": blocked, "total": len(blocked)}
