from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_ledger_db
from ..ledger import LedgerDB
from ..schemas import TraceCreateRequest, TraceEntry, TraceListResponse, TraceUpdateRequest

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.post("", response_model=TraceEntry, status_code=201)
def create_trace(
    payload: TraceCreateRequest,
    ledger: LedgerDB = Depends(get_ledger_db),
) -> TraceEntry:
    """Create a new reasoning trace."""
    trace = TraceEntry(
        trace_id="",
        **payload.model_dump(),
    )
    return ledger.store_trace(trace)


@router.get("", response_model=TraceListResponse)
def list_traces(
    actor: str | None = Query(default=None),
    status: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    capture_id: str | None = Query(default=None),
    page_id: str | None = Query(default=None),
    candidate_id: str | None = Query(default=None),
    policy_ref: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> TraceListResponse:
    """Query reasoning traces by various filters."""
    traces = ledger.list_traces(
        actor=actor,
        status=status,
        task_id=task_id,
        capture_id=capture_id,
        page_id=page_id,
        candidate_id=candidate_id,
        policy_ref=policy_ref,
        limit=limit,
        offset=offset,
    )
    total = ledger.count_traces(
        actor=actor,
        status=status,
        task_id=task_id,
        capture_id=capture_id,
        page_id=page_id,
        candidate_id=candidate_id,
        policy_ref=policy_ref,
    )
    return TraceListResponse(traces=traces, total=total, limit=limit, offset=offset)


@router.get("/{trace_id}", response_model=TraceEntry)
def get_trace(
    trace_id: str,
    ledger: LedgerDB = Depends(get_ledger_db),
) -> TraceEntry:
    """Get a trace by ID."""
    trace = ledger.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


@router.patch("/{trace_id}", response_model=TraceEntry)
def update_trace(
    trace_id: str,
    payload: TraceUpdateRequest,
    ledger: LedgerDB = Depends(get_ledger_db),
) -> TraceEntry:
    """Update an existing trace (e.g., mark completed, add outcome)."""
    existing = ledger.get_trace(trace_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    update_data = payload.model_dump(exclude_unset=True)
    updated = TraceEntry.model_validate(existing.model_dump() | update_data)
    updated.updated_at = datetime.now(timezone.utc).isoformat()
    return ledger.store_trace(updated)
