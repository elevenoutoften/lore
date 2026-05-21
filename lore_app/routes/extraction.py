from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import get_ledger_db, get_repo
from ..extraction import extract_from_captures, get_unprocessed_captures
from ..ledger import LedgerDB
from ..repository import LoreRepository
from ..schemas import (
    ExtractionRequest,
    ExtractionResetRequest,
    ExtractionResetResponse,
    ExtractionResult,
    ExtractionStatusResponse,
)

router = APIRouter()


@router.post("/api/extraction/run", response_model=ExtractionResult)
def api_run_extraction(
    payload: ExtractionRequest,
    repo: LoreRepository = Depends(get_repo),
    ledger_db: LedgerDB = Depends(get_ledger_db),
):
    return extract_from_captures(
        repo,
        capture_ids=payload.capture_ids,
        batch_size=payload.batch_size,
        dry_run=payload.dry_run,
        ledger_db=ledger_db,
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


@router.get("/api/extraction/batches")
def api_extraction_batches(
    limit: int = Query(default=100, ge=1, le=500),
    ledger_db: LedgerDB = Depends(get_ledger_db),
):
    return {"batches": ledger_db.list_batches(limit=limit)}


@router.get("/api/extraction/candidates")
def api_extraction_candidates(
    candidate_type: str | None = Query(default=None, alias="type"),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    ledger_db: LedgerDB = Depends(get_ledger_db),
):
    candidates = ledger_db.get_candidates(candidate_type=candidate_type, status=status, limit=limit)
    return {"count": len(candidates), "candidates": candidates}
