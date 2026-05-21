from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..deps import get_graph_cache, get_metrics, get_repo, get_search_index, get_templates, get_vector_store
from ..link_graph import LinkGraphCache
from ..observability import MetricsCollector
from ..procedure_candidate import find_repeated_captures, propose_procedure_candidate
from ..rag.vector_store import VectorStore
from ..repository import InvalidPageId, LoreRepository
from ..route_utils import index_vectors_for_page, template_context
from ..schemas import (
    ProcedureCandidateProposal,
    ProcedureCandidateResponse,
    RepeatedCaptureGroup,
)
from ..search_index import LoreSearchIndex

router = APIRouter()


@router.get("/api/procedures/candidates", response_model=list[RepeatedCaptureGroup])
def api_find_repeated_captures(repo: LoreRepository = Depends(get_repo)):
    return find_repeated_captures(repo)


@router.post(
    "/api/procedures/candidates",
    response_model=ProcedureCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_propose_procedure_candidate(
    payload: ProcedureCandidateProposal,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    metrics: MetricsCollector = Depends(get_metrics),
):
    try:
        result = propose_procedure_candidate(
            repo,
            payload.capture_ids,
            title=payload.title,
            trigger=payload.trigger,
            lane=payload.lane,
        )
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metrics.increment_index_size()
    search_idx.upsert_page_from_detail(result.page)
    background_tasks.add_task(index_vectors_for_page, vector_store, result.page)
    graph_cache.invalidate()
    return result


@router.get("/procedures", response_class=HTMLResponse)
def procedures_dashboard(
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
):
    groups = find_repeated_captures(repo)

    # List existing procedure-candidate pages.
    candidates = [
        page for page in repo.list_pages(kind="procedure-candidate")
    ]

    enriched_candidates = []
    for candidate in candidates:
        detail = repo.read_page(candidate.id)
        if detail is None:
            continue
        enriched_candidates.append({
            "page": candidate,
            "sources": [s for s in candidate.sources if s],
            "trigger": detail.frontmatter.get("trigger", ""),
        })

    return templates.TemplateResponse(
        request,
        "procedures.html",
        template_context(
            request,
            groups=groups,
            group_count=len(groups),
            candidates=enriched_candidates,
            candidate_count=len(enriched_candidates),
            title="Procedure Candidates",
        ),
    )
