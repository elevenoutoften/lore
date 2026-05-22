from __future__ import annotations

from fastapi import APIRouter, Depends

from ..context_graph import build_context_graph, explain_context, query_neighbors, query_paths
from ..deps import get_ledger_db, get_repo
from ..ledger import LedgerDB
from ..repository import LoreRepository
from ..schemas import (
    ContextExplainQuery,
    ContextExplainResponse,
    ContextGraph,
    ContextGraphNeighborQuery,
    ContextGraphNeighborResponse,
    ContextGraphPathQuery,
    ContextGraphPathResponse,
)

router = APIRouter(prefix="/api/context-graph", tags=["context-graph"])


@router.get("", response_model=ContextGraph)
def get_context_graph(
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> ContextGraph:
    return build_context_graph(repo, ledger)


@router.post("/neighbors", response_model=ContextGraphNeighborResponse)
def post_neighbors(
    query: ContextGraphNeighborQuery,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> ContextGraphNeighborResponse:
    graph = build_context_graph(repo, ledger)
    return query_neighbors(graph, query)


@router.post("/paths", response_model=ContextGraphPathResponse)
def post_paths(
    query: ContextGraphPathQuery,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> ContextGraphPathResponse:
    graph = build_context_graph(repo, ledger)
    return query_paths(graph, query)


@router.post("/explain", response_model=ContextExplainResponse)
def post_explain(
    query: ContextExplainQuery,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> ContextExplainResponse:
    graph = build_context_graph(repo, ledger)
    return explain_context(graph, query)
