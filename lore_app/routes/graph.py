from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..analytics import GraphAnalytics, GraphAnalyticsResult
from ..context_graph import build_context_graph
from ..deps import get_graph_cache, get_ledger_db, get_repo, get_templates
from ..ledger import LedgerDB
from ..link_graph import LinkGraphCache, build_enriched_graph, build_source_edges, page_links
from ..repository import InvalidPageId, LoreRepository
from ..route_utils import template_context
from ..schemas import EnrichedLinkGraphResponse, LinkEdge, LinkGraphResponse, PageLinks

router = APIRouter()


@router.get("/api/links", response_model=LinkGraphResponse)
def api_link_graph(repo: LoreRepository = Depends(get_repo), graph_cache: LinkGraphCache = Depends(get_graph_cache)):
    return graph_cache.get(repo)


@router.get("/api/graph/stats")
def api_graph_stats(repo: LoreRepository = Depends(get_repo), graph_cache: LinkGraphCache = Depends(get_graph_cache)):
    graph = graph_cache.get(repo)
    return {
        "pages": len(graph.pages),
        "links": len(graph.links),
        "broken_links": len(graph.broken_links),
    }


@router.get("/api/graph/enriched", response_model=EnrichedLinkGraphResponse)
def api_enriched_graph(repo: LoreRepository = Depends(get_repo), graph_cache: LinkGraphCache = Depends(get_graph_cache)):
    return build_enriched_graph(repo, graph_cache)


@router.get("/api/graph/sources", response_model=list[LinkEdge])
def api_source_edges(repo: LoreRepository = Depends(get_repo)):
    return build_source_edges(repo)


@router.get("/api/graph/analytics", response_model=GraphAnalyticsResult)
def api_graph_analytics(
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> GraphAnalyticsResult:
    graph = build_context_graph(repo, ledger)
    return GraphAnalytics(graph).compute()


@router.get("/api/pages/{page_id:path}/links", response_model=PageLinks)
def api_page_links(
    page_id: str,
    repo: LoreRepository = Depends(get_repo),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
):
    try:
        links = page_links(repo, page_id, graph_cache.get(repo))
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if links is None:
        raise HTTPException(status_code=404, detail="Lore page not found.")
    return links


@router.get("/graph", response_class=HTMLResponse)
def graph_view(request: Request, templates: Jinja2Templates = Depends(get_templates)):
    return templates.TemplateResponse(request, "graph.html", template_context(request, title="Link Graph"))
