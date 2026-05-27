from __future__ import annotations

# ruff: noqa: B008
import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..deps import (
    get_audit_log,
    get_code_inventories,
    get_config,
    get_consolidation_worker,
    get_graph_cache,
    get_ledger_db,
    get_metrics,
    get_patch_planner,
    get_repo,
    get_search_index,
    get_vector_store,
)
from ..mcp import dispatch as mcp_dispatch
from ..mcp.tools import WRITE_TOOL_NAMES
from ..route_utils import client_rate_limit_key
from ..routes.admin import package_name

if TYPE_CHECKING:
    from ..audit import AuditLog
    from ..config import LoreConfig
    from ..consolidation_worker import ConsolidationWorker
    from ..ledger import LedgerDB
    from ..link_graph import LinkGraphCache
    from ..observability import MetricsCollector
    from ..patch_planner import PatchPlanner
    from ..rag.vector_store import VectorStore
    from ..repository import LoreRepository
    from ..search_index import LoreSearchIndex

logger = logging.getLogger("lore.mcp")

router = APIRouter()


@router.post("/mcp")
async def mcp(
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    vector_store: VectorStore = Depends(get_vector_store),
    code_inventories: dict = Depends(get_code_inventories),
    config: LoreConfig = Depends(get_config),
    ledger_db: LedgerDB = Depends(get_ledger_db),
    patch_planner: PatchPlanner = Depends(get_patch_planner),
    consolidation_worker: ConsolidationWorker = Depends(get_consolidation_worker),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(mcp_dispatch.exception_response(None, "Request body must be valid JSON."), status_code=400)

    request_id = payload.get("id") if isinstance(payload, dict) else None
    write_call_count = mcp_write_call_count(payload)
    if write_call_count:
        key = client_rate_limit_key(request)
        for _ in range(write_call_count):
            if not request.app.state.write_rate_limiter.check(key):
                return JSONResponse({"detail": "Rate limit exceeded."}, status_code=429)
    try:
        response_payload = mcp_dispatch.handle_mcp_message(
            repo,
            payload,
            search_idx,
            graph_cache,
            vector_store,
            code_inventories,
            config=config,
            ledger_db=ledger_db,
            patch_planner=patch_planner,
            consolidation_worker=consolidation_worker,
            audit_log=audit_log,
            metrics=metrics,
            request=request,
        )
    except mcp_dispatch.JsonRpcError as exc:
        return JSONResponse(mcp_dispatch.error_response(request_id, exc), status_code=200)
    except Exception:
        logger.exception("Unexpected error in MCP handler")
        return JSONResponse(
            mcp_dispatch.exception_response(request_id, "internal error; see server logs"),
            status_code=500,
        )

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
