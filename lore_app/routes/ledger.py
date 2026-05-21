from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import get_ledger_db
from ..extraction import compute_extraction_hash
from ..ledger import LedgerDB, _normalize
from ..schemas import (
    ClaimReinforcementResult,
    ClaimSupersedeResult,
    DecayResult,
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