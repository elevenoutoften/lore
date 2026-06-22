from __future__ import annotations

# ruff: noqa: B008
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status

from ..capture import capture_memory
from ..deps import (
    get_audit_log,
    get_context_graph_cache,
    get_graph_cache,
    get_ledger_db,
    get_lint_config,
    get_metrics,
    get_repo,
    get_search_index,
    get_vector_store,
)
from ..heartbeat import heartbeat_review
from ..post_capture import run_post_capture_side_effects
from ..recall import count_pending_captures, count_scoped_out_claims, recall_hint, weights_for_query
from ..repository import InvalidPageId, LoreRepository
from ..route_utils import recall_actor_scope, record_audit, stamp_capture_actor, validate_content
from ..schemas import (
    CaptureRequest,
    MemoryCaptureRequest,
    MemoryCaptureResponse,
    MemoryHealthResponse,
    MemoryRecallAckRequest,
    MemoryRecallAckResponse,
    MemoryRecallClaim,
    MemoryRecallResponse,
)

if TYPE_CHECKING:
    from ..audit import AuditLog
    from ..context_graph import ContextGraphCache
    from ..ledger import LedgerDB
    from ..link_graph import LinkGraphCache
    from ..lint_config import LintConfig
    from ..observability import MetricsCollector
    from ..rag.vector_store import VectorStore
    from ..search_index import LoreSearchIndex

router = APIRouter()


@router.post("/api/memory/capture", response_model=MemoryCaptureResponse, status_code=status.HTTP_201_CREATED)
def api_memory_capture(
    payload: MemoryCaptureRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    """Optimized endpoint for agent memory writes.

    Accepts a simplified payload designed for high-frequency agent captures
    and maps it to the standard capture pipeline.
    """
    validate_content(payload.text)

    metadata = payload.metadata or {}
    capture_request = CaptureRequest(
        observation=payload.text,
        title=metadata.get("title"),
        namespace=payload.namespace,
        agent=payload.agent_name,
        capture_date=metadata.get("capture_date"),
        source_task=payload.task_id or metadata.get("source_task"),
        related_pages=metadata.get("related_pages") or [],
        confidence=metadata.get("confidence", "unknown"),
        suggested_target_page=metadata.get("suggested_target_page"),
        sources=metadata.get("sources") or [],
        source_paths=metadata.get("source_paths") or [],
        source_urls=metadata.get("source_urls") or [],
        evidence=metadata.get("evidence"),
        epistemic_status=metadata.get("epistemic_status"),
        actor=payload.actor or payload.agent_name,
        lane=payload.lane or metadata.get("lane"),
        task_id=payload.task_id or metadata.get("source_task"),
        decision_id=payload.decision_id or metadata.get("decision_id"),
        trace_id=payload.trace_id or metadata.get("trace_id"),
        tool_calls=payload.tool_calls
        or (metadata.get("tool_calls") if isinstance(metadata.get("tool_calls"), list) else []),
        constraints=payload.constraints
        or (metadata.get("constraints") if isinstance(metadata.get("constraints"), list) else []),
        policies_applied=payload.policies_applied
        or (metadata.get("policies_applied") if isinstance(metadata.get("policies_applied"), list) else []),
    )
    capture_request = stamp_capture_actor(capture_request, request)

    try:
        page = capture_memory(repo, capture_request)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metrics.increment_index_size()
    run_post_capture_side_effects(
        app=request.app,
        page=page,
        search_idx=search_idx,
        vector_store=vector_store,
        graph_cache=graph_cache,
        context_graph_cache=context_graph_cache,
        background_tasks=background_tasks,
    )
    record_audit(
        request,
        audit_log,
        operation="memory_capture",
        page_id=page.id,
        summary=f"Memory capture {page.title}",
        diff_size=len(page.content.encode("utf-8")),
    )

    return MemoryCaptureResponse(
        capture_id=page.id,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get("/api/memory/health", response_model=MemoryHealthResponse)
def api_memory_health(
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    consolidation = ledger.get_consolidation_status()
    heartbeat = heartbeat_review(repo, config=lint_config, graph=graph_cache.get(repo))
    plans_by_status = consolidation.get("plans_by_status") or {}
    last_run = consolidation.get("last_run") or {}
    stuck_runs = consolidation.get("stuck_runs") or []

    pending_captures = sum(1 for page in repo.list_pages(kind="capture") if page.status == "draft")
    # The human-review queue is 'needs_manual_review'; 'review' is not a stored
    # PatchPlanStatus, so the old sum always read 0 for a real manual backlog.
    review_required = int(plans_by_status.get("needs_manual_review", 0)) + int(plans_by_status.get("pending", 0))
    failed_runs = len(stuck_runs)
    if str(last_run.get("status") or "") in {"completed_with_errors", "failed"}:
        failed_runs += 1

    return MemoryHealthResponse(
        pending_captures=pending_captures,
        review_required=review_required,
        rejected_plans=int(plans_by_status.get("rejected", 0)),
        failed_runs=failed_runs,
        last_consolidation=last_run.get("completed_at") or last_run.get("started_at"),
        last_maintenance_at=getattr(request.app.state, "last_maintenance_at", None),
        stale_pages=heartbeat.stale_pages.count,
        contradictions=heartbeat.contradictions.count,
        low_confidence=heartbeat.low_confidence.count,
        expired_facts=heartbeat.expired_facts.count,
        missing_metadata=heartbeat.missing_metadata.count,
        procedure_issues=heartbeat.procedure_issues.count,
        total_issues=heartbeat.total_issues,
    )


@router.get("/api/memory/recall", response_model=MemoryRecallResponse)
def api_memory_recall(
    request: Request,
    query: str | None = Query(
        default=None, description="Optional natural-language query to bias by lexical relevance."
    ),
    subject: str | None = Query(default=None, description="Filter to claims about an exact normalized subject."),
    lane: str | None = Query(default=None, description="Filter to a retrieval lane (project, procedural, ops, ...)."),
    actor: str | None = Query(default=None, description="Filter to claims produced by a specific agent."),
    min_strength: float = Query(default=0.0, ge=0.0, le=1.0, description="Minimum ledger strength to consider."),
    valid_at: str | None = Query(default=None, description="Only claims valid at this ISO timestamp."),
    limit: int = Query(default=20, ge=1, le=200, description="Max claims to return."),
    record_access: bool = Query(
        default=False,
        description="Explicitly stamp access on returned claims. Defaults false so reads are idempotent.",
    ),
    cross_actor: bool = Query(
        default=False,
        description="Admin-only: explicitly allow recall outside the authenticated actor scope.",
    ),
    ledger: LedgerDB = Depends(get_ledger_db),
    repo: LoreRepository = Depends(get_repo),
    metrics: MetricsCollector = Depends(get_metrics),
) -> MemoryRecallResponse:
    """Recency/salience-weighted recall over the claim ledger.

    The agent-facing read surface for durable memory: ranks active claims by
    strength, recency, salience, and (when a query is given) lexical relevance,
    returning the score breakdown so agents can see why each claim surfaced.
    """
    start = time.perf_counter()
    try:
        actor = recall_actor_scope(request, requested_actor=actor, cross_actor=cross_actor)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # record_access stamps access_count/last_accessed_at — a state write. A
    # browser session cookie authorizes safe GETs only, so it must not trigger
    # this telemetry write on a GET; stamping requires a token (or the explicit
    # POST /api/memory/recall/ack, which a cookie cannot reach).
    if record_access and getattr(request.state, "lore_session", False):
        record_access = False
    rows = ledger.recall_claims(
        query=query,
        subject=subject,
        lane=lane,
        actor=actor,
        min_strength=min_strength,
        valid_at=valid_at,
        limit=limit,
        record_access=record_access,
    )
    latency_ms = (time.perf_counter() - start) * 1000.0
    claims = [_recall_row_to_claim(row) for row in rows]

    # Recall hit-rate gauges: every recall counts; a count=0 recall is a miss.
    metrics.increment_recall_requests()
    if not claims:
        metrics.increment_recall_zero_results()

    # Self-diagnosing: a count=0 recall should never be silent. Surface how many
    # captures are still pending consolidation so an agent knows whether memory is
    # genuinely absent or just not consolidated yet. Count only captures not yet
    # extracted — an extracted draft is already recallable as a candidate claim.
    pending_captures = count_pending_captures(repo, ledger)
    # When a scoped recall comes up empty and nothing is pending, check whether the
    # query matches memory owned by *other* actors so the hint can point at actor
    # scope rather than wrongly implying no memory exists.
    scoped_out = 0
    if not claims and pending_captures == 0 and not cross_actor and actor:
        scoped_out = count_scoped_out_claims(
            ledger, query=query, subject=subject, lane=lane, min_strength=min_strength, valid_at=valid_at
        )
    hint = recall_hint(len(claims), pending_captures, scoped_out)

    return MemoryRecallResponse(
        query=query,
        count=len(claims),
        latency_ms=round(latency_ms, 3),
        weights=weights_for_query(
            query,
            semantic_available=bool(query and query.strip()) and ledger.semantic_recall_enabled,
        ),
        claims=claims,
        pending_captures=pending_captures,
        hint=hint,
    )


@router.post("/api/memory/recall/ack", response_model=MemoryRecallAckResponse)
def api_memory_recall_ack(
    payload: MemoryRecallAckRequest,
    request: Request,
    ledger: LedgerDB = Depends(get_ledger_db),
) -> MemoryRecallAckResponse:
    """Explicitly acknowledge recall claims that a caller used.

    This is the opt-in write path for salience boosts. Plain GET recall remains
    idempotent and does not mutate access counters.
    """
    timestamp = datetime.now(UTC).isoformat()
    try:
        actor = recall_actor_scope(request, requested_actor=payload.actor, cross_actor=payload.cross_actor)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    acknowledged_count = ledger.record_claim_access(
        payload.candidate_ids, now=timestamp, actor=actor, include_unattributed=True
    )
    return MemoryRecallAckResponse(acknowledged_count=acknowledged_count, timestamp=timestamp)


def _recall_row_to_claim(row: dict[str, Any]) -> MemoryRecallClaim:
    content = row.get("content_json")
    subject = predicate = object_text = ""
    if isinstance(content, dict):
        subject = str(content.get("subject") or "")
        predicate = str(content.get("predicate") or "")
        object_text = str(content.get("object") or "")
    signals = row.get("recall_signals") if isinstance(row.get("recall_signals"), dict) else {}
    return MemoryRecallClaim(
        candidate_id=str(row.get("candidate_id") or ""),
        subject=subject,
        predicate=predicate,
        object=object_text,
        status=str(row.get("status") or "candidate"),
        confidence=_optional_str(row.get("confidence")),
        actor=_optional_str(row.get("actor")),
        lane=_optional_str(row.get("lane")),
        strength=float(row.get("strength") or 0.0),
        access_count=int(row.get("access_count") or 0),
        age_days=float(row.get("age_days") or 0.0),
        recall_score=float(row.get("recall_score") or 0.0),
        recall_signals=signals,
        source_page_ids=_string_list(row.get("source_page_ids")),
        source_capture_ids=_string_list(row.get("source_capture_ids")),
        valid_from=_optional_str(row.get("valid_from")),
        valid_until=_optional_str(row.get("valid_until")),
        updated_at=_optional_str(row.get("updated_at")),
    )


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item)]
    return []
