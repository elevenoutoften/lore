from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..context_graph import (
    DEFAULT_CONTEXT_GRAPH_NODES,
    ContextGraphCache,
    explain_context,
    filter_context_graph,
    query_neighbors,
    query_paths,
    scope_context_graph,
    truncate_context_graph,
)
from ..deps import get_context_graph_cache, get_ledger_db, get_repo
from ..route_utils import recall_actor_scope
from ..schemas import (
    ContextExplainQuery,
    ContextExplainResponse,
    ContextGraph,
    ContextGraphNeighborQuery,
    ContextGraphNeighborResponse,
    ContextGraphPathQuery,
    ContextGraphPathResponse,
)

if TYPE_CHECKING:
    from ..ledger import LedgerDB
    from ..repository import LoreRepository

router = APIRouter(prefix="/api/context-graph", tags=["context-graph"])


@router.get("", response_model=ContextGraph)
def get_context_graph(
    request: Request,
    actor: str | None = Query(default=None),
    cross_actor: bool = Query(default=False),
    limit: int = Query(
        default=DEFAULT_CONTEXT_GRAPH_NODES,
        ge=1,
        le=20000,
        description="Max nodes to return (highest-degree first). Bounds the render payload so large vaults don't freeze the browser; stats.truncated/total_nodes report when the view is partial.",
    ),
    node_types: list[str] | None = Query(
        default=None,
        description="Restrict the render payload to these node types (e.g. node_types=page for the pages-only knowledge map). Applied before the limit; traversal endpoints are unaffected.",
    ),
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextGraph:
    graph = scope_context_graph(context_graph_cache.get(repo, ledger), _actor_scope(request, actor, cross_actor))
    graph = filter_context_graph(graph, node_types)
    return truncate_context_graph(graph, limit)


@router.post("/neighbors", response_model=ContextGraphNeighborResponse)
def post_neighbors(
    query: ContextGraphNeighborQuery,
    request: Request,
    actor: str | None = Query(default=None),
    cross_actor: bool = Query(default=False),
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextGraphNeighborResponse:
    graph = scope_context_graph(context_graph_cache.get(repo, ledger), _actor_scope(request, actor, cross_actor))
    return query_neighbors(graph, query)


@router.post("/paths", response_model=ContextGraphPathResponse)
def post_paths(
    query: ContextGraphPathQuery,
    request: Request,
    actor: str | None = Query(default=None),
    cross_actor: bool = Query(default=False),
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextGraphPathResponse:
    graph = scope_context_graph(context_graph_cache.get(repo, ledger), _actor_scope(request, actor, cross_actor))
    return query_paths(graph, query)


@router.post("/explain", response_model=ContextExplainResponse)
def post_explain(
    query: ContextExplainQuery,
    request: Request,
    actor: str | None = Query(default=None),
    cross_actor: bool = Query(default=False),
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextExplainResponse:
    graph = scope_context_graph(context_graph_cache.get(repo, ledger), _actor_scope(request, actor, cross_actor))
    return explain_context(graph, query)


def _actor_scope(request: Request, actor: str | None, cross_actor: bool) -> str | None:
    try:
        return recall_actor_scope(request, requested_actor=actor, cross_actor=cross_actor)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
