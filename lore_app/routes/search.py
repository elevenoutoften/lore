from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from ..deps import (
    get_context_graph_cache,
    get_graph_cache,
    get_ledger_db,
    get_repo,
    get_search_index,
    get_templates,
    get_vector_store,
)
from ..route_utils import rebuild_vector_index, template_context
from ..schemas import SearchResponse

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from ..context_graph import ContextGraphCache
    from ..ledger import LedgerDB
    from ..link_graph import LinkGraphCache
    from ..rag.vector_store import VectorStore
    from ..repository import LoreRepository
    from ..search_index import LoreSearchIndex

router = APIRouter()


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = Query(default=""),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    templates: Jinja2Templates = Depends(get_templates),
):
    # Tolerate an empty/missing query (browser GET forms can submit one) and render
    # the empty-results state instead of ejecting the user into a raw JSON 422.
    query = q.strip()
    hits = search_idx.search_bm25(query, kind=kind, limit=limit) if query else []
    return templates.TemplateResponse(
        request,
        "search.html",
        template_context(
            request,
            query=q,
            kind=kind or "",
            hits=hits,
            hit_count=len(hits),
            title=f"Search: {q}" if query else "Search",
        ),
    )


@router.get("/api/search", response_model=SearchResponse)
def api_search(
    q: str = Query(min_length=1),
    kind: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
):
    return repo.search(q, kind=kind, visibility=visibility, limit=limit, ledger=ledger)


@router.post("/api/search/reindex")
def api_reindex(
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
):
    count = search_idx.rebuild(repo)
    vector_count = rebuild_vector_index(repo, vector_store)
    graph_cache.invalidate()
    context_graph_cache.invalidate()
    return {"indexed": count, "vector_indexed": vector_count}


@router.get("/api/search/fts")
def api_fts_search(
    q: str = Query(min_length=1),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    search_idx: LoreSearchIndex = Depends(get_search_index),
):
    results = search_idx.search(q, kind=kind, limit=limit)
    return {"query": q, "hits": results}


@router.get("/api/search/bm25")
def api_bm25_search(
    q: str = Query(min_length=1),
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    search_idx: LoreSearchIndex = Depends(get_search_index),
):
    results = search_idx.search_bm25(q, kind=kind, limit=limit)
    return {"query": q, "hits": results}
