from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..deps import get_graph_cache, get_lint_config, get_repo, get_templates
from ..link_graph import LinkGraphCache
from ..lint import lint_contradiction_review, lint_lore, lint_stale_queue
from ..lint_config import LintConfig
from ..repository import LoreRepository
from ..route_utils import template_context
from ..schemas import ContradictionReviewResponse, LoreLintResponse, StalePagesResponse

router = APIRouter()


@router.get("/api/lint", response_model=LoreLintResponse)
def api_lint(
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    return lint_lore(repo, config=lint_config, graph=graph_cache.get(repo))


@router.get("/api/lint/fixable")
def api_lint_fixable(
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    result = lint_lore(repo, config=lint_config, graph=graph_cache.get(repo))
    fixable = [issue for issue in result.issues if issue.auto_fixable and not issue.suppressed]
    return {"fixable_count": len(fixable), "issues": fixable}


@router.get("/api/lint/stale", response_model=StalePagesResponse)
def api_lint_stale(repo: LoreRepository = Depends(get_repo)):
    return lint_stale_queue(repo)


@router.get("/api/lint/contradictions", response_model=ContradictionReviewResponse)
def api_lint_contradictions(repo: LoreRepository = Depends(get_repo)):
    return lint_contradiction_review(repo)


@router.get("/lint", response_class=HTMLResponse)
def lint_dashboard(
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    lint_config: LintConfig = Depends(get_lint_config),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    templates: Jinja2Templates = Depends(get_templates),
):
    result = lint_lore(repo, config=lint_config, graph=graph_cache.get(repo))
    return templates.TemplateResponse(
        request,
        "lint.html",
        template_context(
            request,
            request=request,
            title="Lint Dashboard",
            result=result,
        ),
    )
