from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from ..deps import get_ledger_db, get_repo
from ..precedent_search import search_precedents
from ..schemas import PrecedentSearchRequest, PrecedentSearchResponse

if TYPE_CHECKING:
    from ..ledger import LedgerDB
    from ..repository import LoreRepository

router = APIRouter(prefix="/api/precedents", tags=["precedents"])


@router.post("", response_model=PrecedentSearchResponse)
def search_precedents_api(
    payload: PrecedentSearchRequest,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> PrecedentSearchResponse:
    return search_precedents(repo, ledger, payload)
