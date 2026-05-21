from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..audit import AuditLog
from ..capture import build_capture_digest, build_promotion_audit, capture_memory, list_captures, promote_capture, transition_capture_status
from ..deps import get_audit_log, get_graph_cache, get_metrics, get_repo, get_search_index, get_templates, get_vector_store
from ..link_graph import LinkGraphCache
from ..observability import MetricsCollector
from ..rag.vector_store import VectorStore
from ..repository import InvalidPageId, LoreRepository, optional_string, string_list
from ..route_utils import index_vectors_for_page, record_audit, template_context, validate_content, validate_optional_content, validate_optional_page_id_input, validate_page_id_input
from ..schemas import CaptureDigestResponse, CaptureListResponse, CapturePromotion, CaptureRequest, CaptureResponse, CaptureStatusUpdate, PageDetail, PromotionAuditResponse
from ..search_index import LoreSearchIndex

router = APIRouter()


@router.get("/api/captures/digest", response_model=CaptureDigestResponse)
def api_capture_digest(repo: LoreRepository = Depends(get_repo)):
    return build_capture_digest(repo)


@router.get("/api/captures", response_model=CaptureListResponse)
def api_list_captures(
    status: str | None = Query(default="draft"),
    limit: int = Query(default=50, ge=1, le=200),
    repo: LoreRepository = Depends(get_repo),
):
    return list_captures(repo, status=status, limit=limit)


@router.get("/api/promotions", response_model=PromotionAuditResponse)
def api_promotion_audit(repo: LoreRepository = Depends(get_repo)):
    return build_promotion_audit(repo)


@router.post("/api/capture", response_model=CaptureResponse, status_code=status.HTTP_201_CREATED)
def api_capture(
    payload: CaptureRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    validate_content(payload.observation)
    validate_optional_content(payload.evidence)
    for related_page_id in payload.related_pages:
        validate_page_id_input(related_page_id)
    validate_optional_page_id_input(payload.suggested_target_page)
    try:
        page = capture_memory(repo, payload)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metrics.increment_index_size()
    search_idx.upsert_page_from_detail(page)
    background_tasks.add_task(index_vectors_for_page, vector_store, page)
    graph_cache.invalidate()
    record_audit(
        request,
        audit_log,
        operation="capture",
        page_id=page.id,
        summary=f"Captured {page.title}",
        diff_size=len(page.content.encode("utf-8")),
    )
    return CaptureResponse(page=page)


@router.get("/captures", response_class=HTMLResponse)
def captures_dashboard(
    request: Request,
    status: Literal["draft", "review", "accepted", "rejected", "archived", "all"] = Query(default="draft"),
    repo: LoreRepository = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
):
    capture_list = list_captures(repo, status=status)
    captures = []
    for capture in capture_list.captures:
        detail = repo.read_page(capture.id)
        if detail is None:
            continue
        frontmatter = detail.frontmatter
        confidence = optional_string(frontmatter.get("confidence")) or "unknown"
        captures.append(
            {
                "page": capture,
                "captured_at": optional_string(frontmatter.get("captured_at")),
                "confidence": confidence,
                "confidence_key": confidence.casefold(),
                "source_task": optional_string(frontmatter.get("source_task")),
                "suggested_target_page": optional_string(frontmatter.get("suggested_target_page")),
                "related": string_list(frontmatter.get("related")),
                "summary": optional_string(frontmatter.get("summary")) or capture.summary,
            }
        )

    return templates.TemplateResponse(
        request,
        "captures.html",
        template_context(
            request,
            captures=captures,
            capture_count=len(captures),
            status=status,
            statuses=[
                ("all", "All"),
                ("draft", "Draft"),
                ("review", "Audit"),
                ("accepted", "Accepted"),
                ("rejected", "Rejected"),
                ("archived", "Archived"),
            ],
            title="Capture Queue",
        ),
    )


@router.post("/api/captures/{page_id:path}/status", response_model=PageDetail)
def api_capture_status(
    page_id: str,
    payload: CaptureStatusUpdate,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    validate_page_id_input(page_id)
    try:
        page = transition_capture_status(repo, page_id, payload.status)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    search_idx.upsert_page_from_detail(page)
    background_tasks.add_task(index_vectors_for_page, vector_store, page)
    graph_cache.invalidate()
    return page


@router.post("/api/captures/{page_id:path}/promote", response_model=PageDetail)
def api_capture_promote(
    page_id: str,
    payload: CapturePromotion,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    validate_page_id_input(page_id)
    validate_optional_page_id_input(payload.target_page_id)
    validate_optional_content(payload.content)
    target_existed = None
    try:
        capture_before = repo.read_page(page_id)
        if capture_before is not None:
            target_page_id = payload.target_page_id or optional_string(capture_before.frontmatter.get("suggested_target_page"))
            if target_page_id:
                target_existed = repo.read_page(target_page_id) is not None
    except InvalidPageId:
        target_existed = None
    try:
        page = promote_capture(repo, page_id, target_page_id=payload.target_page_id, content=payload.content)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if target_existed is False:
        metrics.increment_index_size()
    search_idx.upsert_page_from_detail(page)
    background_tasks.add_task(index_vectors_for_page, vector_store, page)
    capture_page = repo.read_page(page_id)
    if capture_page is not None:
        search_idx.upsert_page_from_detail(capture_page)
        background_tasks.add_task(index_vectors_for_page, vector_store, capture_page)
    graph_cache.invalidate()
    diff_size = len(page.content.encode("utf-8"))
    if capture_page is not None:
        diff_size += len(capture_page.content.encode("utf-8"))
    record_audit(
        request,
        audit_log,
        operation="promote",
        page_id=page.id,
        summary=f"Promoted capture {page_id} to {page.id}",
        diff_size=diff_size,
    )
    return page
