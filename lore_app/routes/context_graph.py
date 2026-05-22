from __future__ import annotations

from fastapi import APIRouter, Depends

from ..context_graph import build_context_graph
from ..deps import get_ledger_db, get_repo
from ..ledger import LedgerDB
from ..repository import LoreRepository
from ..schemas import ContextGraph

router = APIRouter(prefix="/api/context-graph", tags=["context-graph"])


@router.get("", response_model=ContextGraph)
def get_context_graph(
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> ContextGraph:
    return build_context_graph(repo, ledger)
