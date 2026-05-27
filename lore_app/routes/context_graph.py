from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from ..context_graph import ContextGraphCache, explain_context, query_neighbors, query_paths
from ..deps import get_context_graph_cache, get_ledger_db, get_repo
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
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextGraph:
    return context_graph_cache.get(repo, ledger)


@router.post("/neighbors", response_model=ContextGraphNeighborResponse)
def post_neighbors(
    query: ContextGraphNeighborQuery,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextGraphNeighborResponse:
    graph = context_graph_cache.get(repo, ledger)
    return query_neighbors(graph, query)


@router.post("/paths", response_model=ContextGraphPathResponse)
def post_paths(
    query: ContextGraphPathQuery,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextGraphPathResponse:
    graph = context_graph_cache.get(repo, ledger)
    return query_paths(graph, query)


@router.post("/explain", response_model=ContextExplainResponse)
def post_explain(
    query: ContextExplainQuery,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
) -> ContextExplainResponse:
    graph = context_graph_cache.get(repo, ledger)
    return explain_context(graph, query)
