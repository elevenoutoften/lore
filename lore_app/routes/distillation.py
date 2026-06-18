from __future__ import annotations

# ruff: noqa: B008
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..deps import get_repo
from ..distillation import distill_daily, get_daily_captures, get_pending_days, promote_daily_note
from ..repository import InvalidPageId, LoreRepository
from ..schemas import DailyDistillRequest, DailyDistillResponse, PageSummary, PendingDaysResponse

router = APIRouter()


@router.post("/api/distill/daily", response_model=DailyDistillResponse)
def api_distill_daily(
    request: Request,
    payload: DailyDistillRequest | None = None,
    repo: LoreRepository = Depends(get_repo),
):
    if payload is None:
        payload = DailyDistillRequest()
    try:
        return distill_daily(repo, payload, llm_client=getattr(request.app.state, "llm_client", None))
    except InvalidPageId as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/api/distill/daily/{target_date}", response_model=list[PageSummary])
def api_get_daily_captures(
    target_date: str,
    repo: LoreRepository = Depends(get_repo),
):
    try:
        parsed = date.fromisoformat(target_date[:10])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date must be ISO format YYYY-MM-DD.",
        ) from exc
    return get_daily_captures(repo, parsed)


@router.post("/api/distill/promote/{target_date}", status_code=status.HTTP_200_OK)
def api_promote_daily(
    target_date: str,
    repo: LoreRepository = Depends(get_repo),
):
    try:
        parsed = date.fromisoformat(target_date[:10])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date must be ISO format YYYY-MM-DD.",
        ) from exc
    try:
        page_id = promote_daily_note(repo, parsed)
    except InvalidPageId as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"page_id": page_id, "status": "promoted"}


@router.get("/api/distill/pending", response_model=PendingDaysResponse)
def api_pending_days(repo: LoreRepository = Depends(get_repo)):
    return get_pending_days(repo)
