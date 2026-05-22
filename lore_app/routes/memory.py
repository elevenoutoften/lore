from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from ..capture import capture_memory
from ..deps import (
    get_audit_log,
    get_graph_cache,
    get_ledger_db,
    get_lint_config,
    get_metrics,
    get_repo,
    get_search_index,
    get_vector_store,
)
from ..heartbeat import heartbeat_review
from ..ledger import LedgerDB
from ..link_graph import LinkGraphCache
from ..lint_config import LintConfig
from ..observability import MetricsCollector
from ..rag.vector_store import VectorStore
from ..repository import InvalidPageId, LoreRepository
from ..route_utils import index_vectors_for_page, record_audit, validate_content
from ..schemas import CaptureRequest, MemoryCaptureRequest, MemoryCaptureResponse, MemoryHealthResponse
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
    audit_log: "AuditLog" = Depends(get_audit_log),
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

    try:
        page = capture_memory(repo, capture_request)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metrics.increment_index_size()
    search_idx.upsert_page_from_detail(page)
    background_tasks.add_task(index_vectors_for_page, vector_store, page)
    graph_cache.invalidate()
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
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/api/memory/health", response_model=MemoryHealthResponse)
def api_memory_health(
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
    review_required = int(plans_by_status.get("pending", 0)) + int(plans_by_status.get("review", 0))
    failed_runs = len(stuck_runs)
    if str(last_run.get("status") or "") in {"completed_with_errors", "failed"}:
        failed_runs += 1

    return MemoryHealthResponse(
        pending_captures=pending_captures,
        review_required=review_required,
        rejected_plans=int(plans_by_status.get("rejected", 0)),
        failed_runs=failed_runs,
        last_consolidation=last_run.get("completed_at") or last_run.get("started_at"),
        stale_pages=heartbeat.stale_pages.count,
        contradictions=heartbeat.contradictions.count,
        low_confidence=heartbeat.low_confidence.count,
        expired_facts=heartbeat.expired_facts.count,
        missing_metadata=heartbeat.missing_metadata.count,
        procedure_issues=heartbeat.procedure_issues.count,
        total_issues=heartbeat.total_issues,
    )
