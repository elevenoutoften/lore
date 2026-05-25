from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

PROTOCOL_VERSION = "2025-11-25"
PROJECT_NAME = "lore"


def package_version() -> str:
    try:
        return version(PROJECT_NAME)
    except PackageNotFoundError:
        return "0.3.0b1"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def handle_mcp_message(
    repo: Any,
    message: Any,
    search_index: Any | None = None,
    graph_cache: Any | None = None,
    vector_store: Any | None = None,
    code_inventories: dict[str, Any] | None = None,
    *,
    config: Any | None = None,
    ledger_db: Any | None = None,
    patch_planner: Any | None = None,
    consolidation_worker: Any | None = None,
    audit_log: Any | None = None,
    metrics: Any | None = None,
) -> Any | None:
    if isinstance(message, list):
        responses = [
            response
            for item in message
            if (
                response := handle_mcp_message(
                    repo,
                    item,
                    search_index,
                    graph_cache,
                    vector_store,
                    code_inventories,
                    config=config,
                    ledger_db=ledger_db,
                    patch_planner=patch_planner,
                    consolidation_worker=consolidation_worker,
                    audit_log=audit_log,
                    metrics=metrics,
                )
            )
            is not None
        ]
        return responses

    if not isinstance(message, dict):
        raise JsonRpcError(-32600, "Invalid JSON-RPC request.")

    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        raise JsonRpcError(-32600, "Invalid JSON-RPC request.")
    if not isinstance(params, dict):
        raise JsonRpcError(-32602, "Request params must be an object.")

    try:
        result = handle_mcp_request(
            repo,
            method,
            params,
            search_index,
            graph_cache,
            vector_store,
            code_inventories,
            config=config,
            ledger_db=ledger_db,
            patch_planner=patch_planner,
            consolidation_worker=consolidation_worker,
            audit_log=audit_log,
            metrics=metrics,
        )
    except JsonRpcError as exc:
        return error_response(request_id, exc)

    if request_id is None:
        return None
    return success_response(request_id, result)


def handle_mcp_request(
    repo: Any,
    method: str,
    params: dict[str, Any],
    search_index: Any | None = None,
    graph_cache: Any | None = None,
    vector_store: Any | None = None,
    code_inventories: dict[str, Any] | None = None,
    *,
    config: Any | None = None,
    ledger_db: Any | None = None,
    patch_planner: Any | None = None,
    consolidation_worker: Any | None = None,
    audit_log: Any | None = None,
    metrics: Any | None = None,
) -> dict[str, Any]:
    from .prompts import list_prompts
    from .resources import list_resource_templates, list_resources, read_resource
    from .resources import require_string
    from .tools import TOOLS, call_tool

    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": PROJECT_NAME, "version": package_version()},
        }

    if method == "ping":
        return {}

    if method == "notifications/initialized":
        return {}

    if method == "tools/list":
        return {"tools": TOOLS}

    if method == "tools/call":
        return call_tool(
            repo,
            params,
            search_index,
            graph_cache,
            vector_store,
            code_inventories,
            config=config,
            ledger_db=ledger_db,
            patch_planner=patch_planner,
            consolidation_worker=consolidation_worker,
            audit_log=audit_log,
            metrics=metrics,
        )

    if method == "resources/list":
        return list_resources(repo)

    if method == "resources/read":
        return read_resource(repo, require_string(params.get("uri"), "uri"))

    if method == "resources/templates/list":
        return list_resource_templates()

    if method == "prompts/list":
        return list_prompts()

    raise JsonRpcError(-32601, f"Unsupported MCP method: {method}")


def success_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, exc: JsonRpcError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": exc.code, "message": exc.message},
    }
    if exc.data is not None:
        payload["error"]["data"] = exc.data
    return payload


def exception_response(request_id: Any, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32603, "message": message},
    }
