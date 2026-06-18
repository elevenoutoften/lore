from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_ledger_db, get_repo
from ..precedent_search import search_precedents
from ..route_utils import recall_actor_scope
from ..schemas import PrecedentSearchRequest, PrecedentSearchResponse

if TYPE_CHECKING:
    from ..ledger import LedgerDB
    from ..repository import LoreRepository

router = APIRouter(prefix="/api/precedents", tags=["precedents"])


@router.post("", response_model=PrecedentSearchResponse)
def search_precedents_api(
    payload: PrecedentSearchRequest,
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
) -> PrecedentSearchResponse:
    try:
        actor = recall_actor_scope(request, requested_actor=payload.actor, cross_actor=False)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    payload = payload.model_copy(update={"actor": actor})
    return search_precedents(repo, ledger, payload)
