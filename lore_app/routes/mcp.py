from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..deps import (
    get_code_inventories,
    get_consolidation_worker,
    get_graph_cache,
    get_ledger_db,
    get_patch_planner,
    get_repo,
    get_search_index,
    get_vector_store,
)
from ..consolidation_worker import ConsolidationWorker
from ..ledger import LedgerDB
from ..link_graph import LinkGraphCache
from ..mcp import WRITE_TOOL_NAMES, exception_response, handle_mcp_message
from ..patch_planner import PatchPlanner
from ..rag.vector_store import VectorStore
from ..repository import LoreRepository
from ..route_utils import client_rate_limit_key
from ..routes.admin import package_name
from ..search_index import LoreSearchIndex

router = APIRouter()


@router.post("/mcp")
async def mcp(
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    vector_store: VectorStore = Depends(get_vector_store),
    code_inventories: dict = Depends(get_code_inventories),
    ledger_db: LedgerDB = Depends(get_ledger_db),
    patch_planner: PatchPlanner = Depends(get_patch_planner),
    consolidation_worker: ConsolidationWorker = Depends(get_consolidation_worker),
):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(exception_response(None, "Request body must be valid JSON."), status_code=400)

    request_id = payload.get("id") if isinstance(payload, dict) else None
    write_call_count = mcp_write_call_count(payload)
    if write_call_count:
        key = client_rate_limit_key(request)
        for _ in range(write_call_count):
            if not request.app.state.write_rate_limiter.check(key):
                return JSONResponse({"detail": "Rate limit exceeded."}, status_code=429)
    try:
        response_payload = handle_mcp_message(
            repo,
            payload,
            search_idx,
            graph_cache,
            vector_store,
            code_inventories,
            ledger_db=ledger_db,
            patch_planner=patch_planner,
            consolidation_worker=consolidation_worker,
        )
    except Exception as exc:
        return JSONResponse(exception_response(request_id, str(exc)), status_code=200)

    if response_payload is None:
        return Response(status_code=202)
    return JSONResponse(response_payload)


def mcp_write_call_count(payload: object) -> int:
    if isinstance(payload, list):
        return sum(mcp_write_call_count(item) for item in payload)
    if not isinstance(payload, dict):
        return 0
    if payload.get("method") != "tools/call":
        return 0
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return 0
    return 1 if params.get("name") in WRITE_TOOL_NAMES else 0


@router.get("/mcp")
def mcp_info():
    return {
        "name": package_name(),
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "methods": [
            "initialize",
            "tools/list",
            "tools/call",
            "resources/list",
            "resources/read",
            "resources/templates/list",
        ],
    }
