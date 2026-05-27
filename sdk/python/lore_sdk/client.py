from __future__ import annotations

import json
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

from .exceptions import LoreError


class LoreClient:
    def __init__(self, base_url: str, auth_token: str | None = None, timeout: int = 30):
        """Initialize Lore client.

        Args:
            base_url: Lore server URL (e.g. "http://localhost:8078")
            auth_token: Optional bearer token for authenticated endpoints
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

    def health(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/healthz")

    def version(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/version")

    def config(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/config")

    def list_pages(
        self,
        category: str | None = None,
        tag: str | None = None,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | list[Any]:
        params = {
            "category": category,
            "tag": tag,
            "kind": kind,
            "visibility": visibility,
            "q": query,
            "limit": limit,
        }
        return self._request("GET", "/api/pages", params=params)

    def get_page(self, page_id: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", f"/api/pages/{self._path(page_id)}")

    def upsert_page(self, page_id: str, content: str, commit_message: str | None = None) -> dict[str, Any] | list[Any]:
        payload = {"content": content}
        if commit_message is not None:
            payload["commit_message"] = commit_message
        return self._request("PUT", f"/api/pages/{self._path(page_id)}", data=payload)

    def delete_page(self, page_id: str) -> dict[str, Any] | list[Any]:
        return self._request("DELETE", f"/api/pages/{self._path(page_id)}")

    def get_rendered_page(self, page_id: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", f"/api/pages/{self._path(page_id)}/rendered")

    def get_page_links(self, page_id: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", f"/api/pages/{self._path(page_id)}/links")

    def update_metadata(self, page_id: str, metadata: dict[str, Any]) -> dict[str, Any] | list[Any]:
        return self._request("PATCH", f"/api/pages/{self._path(page_id)}/metadata", data=metadata)

    def create_stub(self, page_id: str, title: str | None = None, **metadata: Any) -> dict[str, Any] | list[Any]:
        payload = {key: value for key, value in {"title": title, **metadata}.items() if value is not None}
        return self._request("POST", f"/api/pages/{self._path(page_id)}/stub", data=payload)

    def search(self, query: str, limit: int = 20) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/search", params={"q": query, "limit": limit})

    def search_fts(self, query: str, limit: int = 20) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/search/fts", params={"q": query, "limit": limit})

    def search_bm25(self, query: str, limit: int = 20) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/search/bm25", params={"q": query, "limit": limit})

    def reindex(self) -> dict[str, Any] | list[Any]:
        return self._request("POST", "/api/search/reindex")

    def get_links(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/links")

    def get_graph_stats(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/graph/stats")

    def get_enriched_graph(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/graph/enriched")

    def get_source_edges(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/graph/sources")

    def lint(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/lint")

    def lint_fixable(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/lint/fixable")

    def lint_stale(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/lint/stale")

    def lint_contradictions(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/lint/contradictions")

    def list_captures(
        self,
        status: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any] | list[Any]:
        return self._request(
            "GET", "/api/captures", params={"status": status, "tag": tag, "limit": limit, "offset": offset}
        )

    def create_capture(
        self,
        title: str,
        body: str,
        source: str,
        tags: list[str] | None = None,
        **metadata: Any,
    ) -> dict[str, Any] | list[Any]:
        payload = {
            "title": title,
            "body": body,
            "source": source,
            "tags": tags,
            "observation": body,
            "sources": [source],
            **metadata,
        }
        return self._request("POST", "/api/capture", data=self._without_none(payload))

    def capture_digest(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/captures/digest")

    def get_capture_status(self, page_id: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", f"/api/captures/{self._path(page_id)}/status")

    def update_capture_status(self, page_id: str, status: str) -> dict[str, Any] | list[Any]:
        return self._request("POST", f"/api/captures/{self._path(page_id)}/status", data={"status": status})

    def promote_capture(
        self,
        page_id: str,
        commit_message: str | None = None,
        *,
        target_page_id: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any] | list[Any]:
        payload = self._without_none(
            {
                "commit_message": commit_message,
                "target_page_id": target_page_id,
                "content": content,
            }
        )
        return self._request("POST", f"/api/captures/{self._path(page_id)}/promote", data=payload)

    def promotion_audit(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/promotions")

    def catalog(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/catalog")

    def semantics(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/semantics")

    def frontmatter_spec(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/frontmatter/spec")

    def list_decisions(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/decisions")

    def decision_template(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/decisions/template")

    def list_procedures(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/procedures")

    def procedure_template(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/api/procedures/template")

    def export_procedure(self, page_id: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", f"/api/procedures/{self._path(page_id)}/export")

    def create_trace(
        self,
        *,
        actor: str,
        reason_summary: str,
        status: str = "active",
        parent_trace_id: str | None = None,
        context_refs: list[dict] | None = None,
        tool_refs: list[dict] | None = None,
        constraints: list[str] | None = None,
        policy_refs: list[str] | None = None,
        alternatives: list[dict] | None = None,
        outcome: str = "",
        related_ids: dict | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Create a reasoning trace."""
        payload: dict[str, Any] = {"actor": actor, "reason_summary": reason_summary, "status": status}
        if parent_trace_id:
            payload["parent_trace_id"] = parent_trace_id
        if context_refs:
            payload["context_refs"] = context_refs
        if tool_refs:
            payload["tool_refs"] = tool_refs
        if constraints:
            payload["constraints"] = constraints
        if policy_refs:
            payload["policy_refs"] = policy_refs
        if alternatives:
            payload["alternatives"] = alternatives
        if outcome:
            payload["outcome"] = outcome
        if related_ids:
            payload["related_ids"] = related_ids
        return self._request("POST", "/api/traces", data=payload)

    def get_trace(self, trace_id: str) -> dict[str, Any] | list[Any]:
        """Retrieve a reasoning trace by ID."""
        return self._request("GET", f"/api/traces/{trace_id}")

    def list_traces(
        self,
        *,
        actor: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any] | list[Any]:
        """Query reasoning traces by filters."""
        params: dict[str, Any] = {"limit": limit}
        if actor:
            params["actor"] = actor
        if status:
            params["status"] = status
        if task_id:
            params["task_id"] = task_id
        return self._request("GET", "/api/traces", params=params)

    def code_references(self, code_path: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", f"/api/code-references/{self._path(code_path)}")

    def ingest_service(self, service_id: str, source_dir: str) -> dict[str, Any] | list[Any]:
        return self._request("POST", f"/api/code-ingest/{self._path(service_id)}", params={"source_dir": source_dir})

    def service_inventory(self, service_id: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", f"/api/code-ingest/{self._path(service_id)}/inventory")

    def rag_retrieve(self, query: str, limit: int = 10) -> dict[str, Any] | list[Any]:
        return self._request("POST", "/api/rag/retrieve", data={"query": query, "limit": limit})

    def rag_evaluate(self, query: str, expected_ids: list[str]) -> dict[str, Any] | list[Any]:
        payload = {"queries": [{"query": query, "expected_ids": expected_ids}]}
        return self._request("POST", "/api/rag/evaluate", data=payload)

    def mcp(self, payload: dict[str, Any]) -> dict[str, Any] | list[Any]:
        return self._request("POST", "/mcp", data=payload)

    def mcp_info(self) -> dict[str, Any] | list[Any]:
        return self._request("GET", "/mcp")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        url = self._url(path, params)
        body = None
        headers = {"Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.getcode()
                response_body = response.read()
        except HTTPError as exc:
            message = self._error_message(exc)
            raise LoreError(exc.code, message) from exc
        except URLError as exc:
            raise LoreError(0, str(exc.reason)) from exc

        if not 200 <= status < 300:
            raise LoreError(status, response_body.decode("utf-8", errors="replace"))
        return self._parse_json(response_body)

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        query = urlencode(self._without_none(params or {}), doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            return f"{url}?{query}"
        return url

    @staticmethod
    def _path(value: str) -> str:
        return quote(value.strip("/"), safe="/")

    @staticmethod
    def _without_none(values: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def _parse_json(body: bytes) -> dict[str, Any] | list[Any]:
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _error_message(exc: HTTPError) -> str:
        body = exc.read()
        if not body:
            return exc.reason
        text = body.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("message") or payload.get("error")
            if detail is not None:
                return str(detail)
        return text
