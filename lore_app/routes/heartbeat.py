from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse

from ..deps import (
    get_audit_log,
    get_context_graph_cache,
    get_graph_cache,
    get_lint_config,
    get_metrics,
    get_repo,
    get_search_index,
    get_templates,
    get_vector_store,
)
from ..heartbeat import (
    emit_heartbeat_captures,
    heartbeat_capture_category_for_title,
    heartbeat_capture_category_keys,
    heartbeat_review,
)
from ..route_utils import index_vectors_for_page, record_audit, template_context
from ..schemas import HeartbeatCaptureResponse, HeartbeatResponse

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from ..audit import AuditLog
    from ..context_graph import ContextGraphCache
    from ..link_graph import LinkGraphCache
    from ..lint_config import LintConfig
    from ..observability import MetricsCollector
    from ..rag.vector_store import VectorStore
    from ..repository import LoreRepository
    from ..search_index import LoreSearchIndex

router = APIRouter()


@router.get("/api/heartbeat", response_model=HeartbeatResponse)
def api_heartbeat(
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    return heartbeat_review(repo, config=lint_config, graph=graph_cache.get(repo))


@router.post("/api/heartbeat/captures", response_model=HeartbeatCaptureResponse)
def api_heartbeat_captures(
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    captures = emit_heartbeat_captures(repo, config=lint_config, graph=graph_cache.get(repo))
    for capture in captures:
        metrics.increment_index_size()
        search_idx.upsert_page_from_detail(capture)
        background_tasks.add_task(index_vectors_for_page, vector_store, capture)
        graph_cache.invalidate()
        context_graph_cache.invalidate()
        record_audit(
            request,
            audit_log,
            operation="heartbeat_capture",
            page_id=capture.id,
            summary=f"Captured {capture.title}",
            diff_size=len(capture.content.encode("utf-8")),
        )
    categories_covered = [
        category
        for capture in captures
        if (category := heartbeat_capture_category_for_title(capture.title)) is not None
    ]
    skipped_categories = [
        category for category in heartbeat_capture_category_keys() if category not in categories_covered
    ]
    return HeartbeatCaptureResponse(
        captures=captures,
        categories_covered=categories_covered,
        skipped_categories=skipped_categories,
    )


@router.get("/heartbeat", response_class=HTMLResponse)
def heartbeat_dashboard(
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    templates: Jinja2Templates = Depends(get_templates),
):
    result = heartbeat_review(repo, config=lint_config, graph=graph_cache.get(repo))
    return templates.TemplateResponse(
        request,
        "heartbeat.html",
        template_context(
            request,
            request=request,
            title="Heartbeat Review",
            result=result,
        ),
    )
