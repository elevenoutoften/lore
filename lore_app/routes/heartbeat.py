from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..deps import get_graph_cache, get_lint_config, get_repo, get_templates
from ..heartbeat import (
    emit_heartbeat_captures,
    heartbeat_capture_category_for_title,
    heartbeat_capture_category_keys,
    heartbeat_review,
)
from ..link_graph import LinkGraphCache
from ..lint_config import LintConfig
from ..repository import LoreRepository
from ..route_utils import template_context
from ..schemas import HeartbeatCaptureResponse, HeartbeatResponse

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
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    captures = emit_heartbeat_captures(repo, config=lint_config, graph=graph_cache.get(repo))
    categories_covered = [
        category
        for capture in captures
        if (category := heartbeat_capture_category_for_title(capture.title)) is not None
    ]
    skipped_categories = [
        category
        for category in heartbeat_capture_category_keys()
        if category not in categories_covered
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
