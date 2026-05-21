from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..deps import get_graph_cache, get_lint_config, get_repo, get_templates
from ..heartbeat import heartbeat_review
from ..link_graph import LinkGraphCache
from ..lint_config import LintConfig
from ..repository import LoreRepository
from ..route_utils import template_context
from ..schemas import HeartbeatResponse

router = APIRouter()


@router.get("/api/heartbeat", response_model=HeartbeatResponse)
def api_heartbeat(
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    return heartbeat_review(repo, config=lint_config, graph=graph_cache.get(repo))


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
