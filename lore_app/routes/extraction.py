from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..deps import get_ledger_db, get_repo
from ..extraction import extract_from_captures, get_unprocessed_captures, retry_deadletter
from ..llm_provider import FallbackLLMClient, NoLlmClient
from ..route_utils import recall_actor_scope
from ..schemas import (
    ExtractionRequest,
    ExtractionResetRequest,
    ExtractionResetResponse,
    ExtractionResult,
    ExtractionRetryResponse,
    ExtractionStatusResponse,
)

if TYPE_CHECKING:
    from ..ledger import LedgerDB
    from ..repository import LoreRepository

router = APIRouter()


@router.post("/api/extraction/run", response_model=ExtractionResult)
def api_run_extraction(
    payload: ExtractionRequest,
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    ledger_db: LedgerDB = Depends(get_ledger_db),
):
    llm_client = getattr(request.app.state, "llm_client", None)
    if payload.provider in ("none", "deterministic"):
        llm_client = None
    elif payload.provider == "escalation":
        if isinstance(llm_client, FallbackLLMClient) and llm_client.escalation is not None:
            llm_client = llm_client.escalation
        elif isinstance(llm_client, NoLlmClient):
            pass
        else:
            # No escalation client configured: fail rather than silently running
            # the primary model and mislabelling the result as escalation.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No escalation provider is configured; set an escalation model/API key.",
            )
    return extract_from_captures(
        repo,
        capture_ids=payload.capture_ids,
        batch_size=payload.batch_size,
        dry_run=payload.dry_run,
        ledger_db=ledger_db,
        llm_client=llm_client,
    )


@router.get("/api/extraction/status", response_model=ExtractionStatusResponse)
def api_extraction_status(
    repo: LoreRepository = Depends(get_repo),
    ledger_db: LedgerDB = Depends(get_ledger_db),
):
    base_status = ledger_db.get_extraction_status()
    total_draft = len([page for page in repo.list_pages(kind="capture") if page.status == "draft"])
    total_pending = len(get_unprocessed_captures(repo, limit=200, ledger_db=ledger_db))
    return ExtractionStatusResponse(
        total_draft_captures=total_draft,
        total_extracted=base_status.total_extracted,
        total_pending=total_pending,
        last_batch_id=base_status.last_batch_id,
        last_run_at=base_status.last_run_at,
    )


@router.post("/api/extraction/reset", response_model=ExtractionResetResponse)
def api_reset_extraction(
    payload: ExtractionResetRequest,
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> ExtractionResetResponse:
    reset_count = ledger_db.reset_extraction(capture_ids=payload.capture_ids)
    return ExtractionResetResponse(reset_count=reset_count)


@router.post("/api/extraction/deadletters/{deadletter_id}/retry", response_model=ExtractionRetryResponse)
def api_retry_extraction_deadletter(
    deadletter_id: str,
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> ExtractionRetryResponse:
    result = retry_deadletter(
        repo,
        deadletter_id,
        ledger_db=ledger_db,
        llm_client=getattr(request.app.state, "llm_client", None),
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dead-letter not found.")
    return result


@router.get("/api/extraction/batches")
def api_extraction_batches(
    limit: int = Query(default=100, ge=1, le=500),
    ledger_db: LedgerDB = Depends(get_ledger_db),
):
    return {"batches": ledger_db.list_batches(limit=limit)}


@router.get("/api/extraction/candidates")
def api_extraction_candidates(
    request: Request,
    candidate_type: str | None = Query(default=None, alias="type"),
    status: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    cross_actor: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    ledger_db: LedgerDB = Depends(get_ledger_db),
):
    try:
        actor = recall_actor_scope(request, requested_actor=actor, cross_actor=cross_actor)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    candidates = ledger_db.get_candidates(candidate_type=candidate_type, status=status, actor=actor, limit=limit)
    return {"count": len(candidates), "candidates": candidates}
