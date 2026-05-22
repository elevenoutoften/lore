from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_ledger_db, get_repo
from ..ledger import LedgerDB
from ..precedent_search import search_precedents
from ..repository import LoreRepository
from ..schemas import PrecedentSearchRequest, PrecedentSearchResponse

router = APIRouter(prefix="/api/precedents", tags=["precedents"])


@router.post("", response_model=PrecedentSearchResponse)
def search_precedents_api(
    payload: PrecedentSearchRequest,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> PrecedentSearchResponse:
    return search_precedents(repo, ledger, payload)
