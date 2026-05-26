from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import get_ledger_db
from ..extraction import compute_extraction_hash
from ..ledger import LedgerDB, _normalize
from ..schemas import (
    ClaimReinforcementResult,
    ClaimSupersedeResult,
    DecayResult,
    ExtractedCandidateResponse,
    ExtractedClaim,
    LedgerClaimQuery,
    LedgerReinforceRequest,
    LedgerSupersedeRequest,
)

ledger_router = APIRouter(prefix="/api/ledger", tags=["ledger"])


@ledger_router.post("/reinforce", response_model=ClaimReinforcementResult)
def reinforce_claim(
    request: LedgerReinforceRequest,
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> ClaimReinforcementResult:
    """Reinforce an existing compatible claim or insert a new one."""
    claim = ExtractedClaim(
        subject=request.subject,
        predicate=request.predicate,
        object=request.object,
        confidence=request.confidence,
        actor=request.actor,
        lane=request.lane,
        observed_at=request.observed_at,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
        evidence=request.evidence,
        source_page_ids=request.source_page_ids,
    )
    dedupe_hash = compute_extraction_hash(claim.subject, claim.predicate, claim.object, claim.source_page_ids)
    metadata = {
        "confidence": claim.confidence,
        "actor": claim.actor,
        "lane": claim.lane,
        "observed_at": claim.observed_at,
        "valid_from": claim.valid_from,
        "valid_until": claim.valid_until,
    }
    return ledger_db.reinforce_or_insert_candidate(
        candidate_type="claim",
        candidate=claim,
        dedupe_hash=dedupe_hash,
        batch_id="__manual__",
        source_capture_ids=[],
        source_page_ids=claim.source_page_ids,
        metadata=metadata,
    )


@ledger_router.post("/supersede", response_model=ClaimSupersedeResult)
def supersede_claim(
    request: LedgerSupersedeRequest,
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> ClaimSupersedeResult:
    """Supersede an old claim with a new one."""
    return ledger_db.supersede_candidate(
        old_candidate_id=request.old_candidate_id,
        new_candidate_id=request.new_candidate_id,
        reason=request.reason,
    )


@ledger_router.post("/activate/{candidate_id}")
def activate_candidate(
    candidate_id: str,
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> dict:
    """Activate a candidate claim."""
    ledger_db.activate_candidate(candidate_id)
    return {"candidate_id": candidate_id, "status": "active"}


@ledger_router.post("/reject/{candidate_id}")
def reject_candidate(
    candidate_id: str,
    reason: str | None = Query(default=None),
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> dict:
    """Reject a candidate claim."""
    ledger_db.reject_candidate(candidate_id, reason=reason)
    return {"candidate_id": candidate_id, "status": "rejected", "reason": reason}


@ledger_router.post("/archive/{candidate_id}")
def archive_candidate(
    candidate_id: str,
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> dict:
    """Archive an active claim."""
    ledger_db.archive_candidate(candidate_id)
    return {"candidate_id": candidate_id, "status": "archived"}


@ledger_router.post("/decay", response_model=DecayResult)
def apply_decay(
    days_since_access: int | None = Query(default=None),
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> DecayResult:
    """Apply time-based decay to claim strength."""
    return ledger_db.apply_decay(days_since_access=days_since_access)


@ledger_router.get("/claims")
def get_claims(
    subject: str | None = Query(default=None),
    lane: str | None = Query(default=None),
    min_strength: float = Query(default=0.0),
    valid_at: str | None = Query(default=None),
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> dict:
    """Query active claims with optional filters."""
    claims = ledger_db.get_active_claims(
        subject=subject,
        lane=lane,
        min_strength=min_strength,
        valid_at=valid_at,
    )
    return {"count": len(claims), "claims": claims}


def _row_to_candidate_response(row: dict) -> ExtractedCandidateResponse:
    """Convert a raw row dict to an ExtractedCandidateResponse."""
    content_json = row.get("content_json")
    evidence = None
    if isinstance(content_json, dict):
        evidence = content_json.get("evidence") or content_json.get("object")
    elif isinstance(content_json, str):
        try:
            import json as _json
            parsed = _json.loads(content_json)
            evidence = parsed.get("evidence") or parsed.get("object")
        except Exception:
            pass
    return ExtractedCandidateResponse(
        candidate_id=str(row.get("candidate_id", "")),
        batch_id=str(row.get("batch_id", "")),
        candidate_type=str(row.get("candidate_type", "")),
        status=str(row.get("status", "candidate")),
        confidence=str(row.get("confidence")) if row.get("confidence") else None,
        epistemic_status=str(row.get("epistemic_status")) if row.get("epistemic_status") else None,
        actor=str(row.get("actor")) if row.get("actor") else None,
        lane=str(row.get("lane")) if row.get("lane") else None,
        observed_at=str(row.get("observed_at")) if row.get("observed_at") else None,
        valid_from=str(row.get("valid_from")) if row.get("valid_from") else None,
        valid_until=str(row.get("valid_until")) if row.get("valid_until") else None,
        strength=float(row.get("strength", 0.5)),
        source_capture_ids=list(row.get("source_capture_ids", []) if isinstance(row.get("source_capture_ids"), list) else []),
        source_page_ids=list(row.get("source_page_ids", []) if isinstance(row.get("source_page_ids"), list) else []),
        evidence=evidence,
        model_version=str(row.get("model_version")) if row.get("model_version") else None,
        prompt_hash=str(row.get("prompt_hash")) if row.get("prompt_hash") else None,
        token_usage=row.get("token_usage") if isinstance(row.get("token_usage"), dict) else None,
        content=content_json if isinstance(content_json, dict) else None,
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
    )


@ledger_router.get("/candidates")
def get_ledger_candidates(
    capture_id: str | None = Query(default=None, description="Filter by source capture ID."),
    page_id: str | None = Query(default=None, description="Filter by source page ID."),
    lane: str | None = Query(default=None, description="Filter by retrieval lane."),
    actor: str | None = Query(default=None, description="Filter by agent actor name."),
    status: str | None = Query(default=None, description="Filter by status (candidate, active, rejected, archived, superseded)."),
    candidate_type: str | None = Query(default=None, alias="type", description="Filter by candidate type (claim, entity, edge, invalidation)."),
    limit: int = Query(default=100, ge=1, le=500, description="Max results to return."),
    ledger_db: LedgerDB = Depends(get_ledger_db),
) -> list[ExtractedCandidateResponse]:
    """Get extraction candidates with full provenance and filter support."""
    candidates = ledger_db.get_candidates(
        candidate_type=candidate_type,
        status=status,
        capture_id=capture_id,
        page_id=page_id,
        lane=lane,
        actor=actor,
        limit=limit,
    )
    return [_row_to_candidate_response(c) for c in candidates]
