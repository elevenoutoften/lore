from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class LoreError(RuntimeError):
    pass


def lore_list_pages(
    *,
    kind: str | None = None,
    visibility: str | None = None,
    query: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {}
    if kind:
        params["kind"] = kind
    if visibility:
        params["visibility"] = visibility
    if query:
        params["q"] = query
    if limit:
        params["limit"] = str(limit)
    return _request("GET", "/api/pages", query=params)


def lore_read_page(page_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/pages/{urllib.parse.quote(page_id, safe='/')}")


def lore_search(
    query: str,
    *,
    kind: str | None = None,
    visibility: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    params = {"q": query, "limit": str(limit)}
    if kind:
        params["kind"] = kind
    if visibility:
        params["visibility"] = visibility
    return _request("GET", "/api/search", query=params)


def lore_capture(
    observation: str,
    *,
    title: str | None = None,
    namespace: str = "inbox",
    agent: str | None = None,
    capture_date: str | None = None,
    source_task: str | None = None,
    related_pages: list[str] | None = None,
    confidence: str | None = "unknown",
    suggested_target_page: str | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "observation": observation,
        "namespace": namespace,
        "confidence": confidence,
    }
    optional_fields = {
        "title": title,
        "agent": agent,
        "capture_date": capture_date,
        "source_task": source_task,
        "related_pages": related_pages,
        "suggested_target_page": suggested_target_page,
        "sources": sources,
    }
    body.update({key: value for key, value in optional_fields.items() if value})
    return _request("POST", "/api/capture", body)


def lore_list_captures(*, status: str | None = "draft", limit: int = 50) -> dict[str, Any]:
    params = {"limit": str(limit)}
    if status:
        params["status"] = status
    return _request("GET", "/api/captures", query=params)


def lore_upsert_page(page_id: str, content: str) -> dict[str, Any]:
    return _request("PUT", f"/api/pages/{urllib.parse.quote(page_id, safe='/')}", {"content": content})


def lore_mcp(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    mcp_params = params or {}
    headers = {"Mcp-Method": method}
    mcp_name = mcp_params.get("name") or mcp_params.get("uri")
    if method in {"tools/call", "resources/read", "prompts/get"} and mcp_name:
        headers["Mcp-Name"] = str(mcp_name)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": mcp_params,
    }
    return _request("POST", "/mcp", request, extra_headers=headers)


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    query: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    base_url = os.environ.get("LORE_BASE_URL", "http://localhost:8078").rstrip("/")
    api_key = os.environ.get("LORE_API_KEY", os.environ.get("LORE_AUTH_TOKEN", "")).strip()
    url = base_url + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)

    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LoreError(f"Lore request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LoreError(f"Lore is unreachable: {exc}") from exc

    return json.loads(raw) if raw else {}
