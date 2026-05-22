from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..deps import get_ledger_db, get_repo, get_retrieve_context, get_templates
from ..ledger import LedgerDB
from ..rag.eval_retrieval import evaluate_retrieval
from ..repository import LoreRepository
from ..route_utils import enrich_expanded_results, template_context
from ..schemas import RagEvaluateRequest, RagEvaluateResult, RagExpandRequest, RagExpandedResponse, RagRetrieveRequest

router = APIRouter()


@router.post("/api/rag/retrieve")
def api_rag_retrieve(payload: RagRetrieveRequest, retrieve_context: Callable[[str, int], dict[str, Any]] = Depends(get_retrieve_context)):
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Missing query.")
    limit = max(1, min(int(payload.limit or 10), 50))
    return retrieve_context(query, limit)


@router.post("/api/rag/retrieve-expanded", response_model=RagExpandedResponse)
def api_rag_retrieve_expanded(
    payload: RagExpandRequest,
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    ledger: LedgerDB = Depends(get_ledger_db),
):
    from ..context_graph import build_context_graph
    from ..rag.hybrid_retrieval import hybrid_retrieve_expanded

    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Missing query.")

    result = hybrid_retrieve_expanded(
        query,
        request.app.state.search_index,
        request.app.state.vector_store,
        build_context_graph(repo, ledger),
        ledger,
        limit=payload.limit,
        expand_hops=payload.expand_hops,
        expand_edge_types=payload.expand_edge_types or None,
        include_claims=payload.include_claims,
        include_traces=payload.include_traces,
        include_decisions=payload.include_decisions,
    )
    return enrich_expanded_results(repo, result)


@router.post("/api/rag/evaluate", response_model=RagEvaluateResult)
def api_rag_evaluate(payload: RagEvaluateRequest, retrieve_context: Callable[[str, int], dict[str, Any]] = Depends(get_retrieve_context)):
    if payload.queries is not None:
        queries = payload.queries
    elif payload.query and payload.expected:
        queries = [{"query": payload.query, "relevant_page_ids": payload.expected}]
    else:
        queries = []
    k = max(1, min(int(payload.k or 10), 50))
    return evaluate_retrieval(queries, retrieve_context, k=k)


@router.get("/rag", response_class=HTMLResponse)
def rag_debug(
    request: Request,
    q: str = Query(default=""),
    retrieve_context: Callable[[str, int], dict[str, Any]] = Depends(get_retrieve_context),
    templates: Jinja2Templates = Depends(get_templates),
):
    result = retrieve_context(q, 10) if q else None
    return templates.TemplateResponse(
        request,
        "rag.html",
        template_context(request, title="RAG Debug", query=q, result=result),
    )
