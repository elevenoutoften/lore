from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_ledger_db, get_repo
from ..ledger import LedgerDB
from ..provenance import get_capture_provenance, get_page_provenance
from ..repository import LoreRepository
from ..schemas import ProvenanceRef, ProvenanceResponse

router = APIRouter(prefix="/api/provenance", tags=["provenance"])


@router.get("/{entity_type}/{entity_id:path}", response_model=ProvenanceResponse)
def get_provenance(
    entity_type: Literal["capture", "trace", "page"],
    entity_id: str,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> ProvenanceResponse:
    if entity_type == "capture":
        provenance = get_capture_provenance(repo, entity_id)
        if provenance is None:
            raise HTTPException(status_code=404, detail=f"Capture {entity_id} not found")
        return ProvenanceResponse(entity_type=entity_type, entity_id=entity_id, provenance=provenance)

    if entity_type == "trace":
        trace = ledger.get_trace(entity_id)
        if trace is None:
            raise HTTPException(status_code=404, detail=f"Trace {entity_id} not found")
        return ProvenanceResponse(
            entity_type=entity_type,
            entity_id=entity_id,
            provenance=trace.provenance or ProvenanceRef(),
        )

    return ProvenanceResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        provenance=get_page_provenance(repo, entity_id),
    )
