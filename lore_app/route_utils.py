from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from .audit import AuditLog, new_audit_entry
from .config import LoreConfig, WorkspaceConfig
from .frontmatter import update_frontmatter
from .link_graph import LinkGraphCache
from .rag.chunker import chunk_page
from .rag.hybrid_retrieval import hybrid_retrieve
from .rag.vector_store import VectorStore
from .repository import InvalidPageId, LoreRepository
from .schemas import LinkEdge, PageDetail, PageLinks, PageSummary
from .search_index import LoreSearchIndex
from .security import sanitize_content, sanitize_page_id

GIT_REF_CACHE_TTL_SECONDS = 300
_GIT_REF_CACHE: tuple[str, float] | None = None


def template_context(app_request: Request, **values: Any) -> dict[str, Any]:
    config = app_request.app.state.config
    return {
        **values,
        "app_name": config.app_name,
        "app_description": config.app_description,
        "brand_title": config.brand_title,
        "brand_url": config.brand_url,
        "favicon_url": config.favicon_url,
        "csp_nonce": getattr(app_request.state, "csp_nonce", ""),
    }


def require_page(repo: LoreRepository, page_id: str) -> PageDetail:
    try:
        page = repo.read_page(page_id)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if page is None:
        raise HTTPException(status_code=404, detail="Lore page not found.")
    return page


def workspace_lore_config(base: LoreConfig, workspace: WorkspaceConfig) -> LoreConfig:
    resolved = workspace.resolve(base)
    config = LoreConfig()
    config.app_name = base.app_name
    config.app_description = base.app_description
    config.content_dir = resolved.content_dir or base.content_dir
    config.search_db = resolved.search_db or base.search_db
    config.vector_db = resolved.vector_db or base.vector_db
    config.ledger_db = resolved.ledger_db or base.ledger_db
    config.api_keys_db = base.api_keys_db
    config.host = base.host
    config.port = base.port
    config.auth_mode = base.auth_mode
    config.auth_secret = base.auth_secret
    config.brand_title = base.brand_title
    config.brand_url = base.brand_url
    config.favicon_url = base.favicon_url
    config.write_rate_limit = base.write_rate_limit
    config.write_rate_window_seconds = base.write_rate_window_seconds
    config.audit_retention_days = base.audit_retention_days
    config.trusted_headers = base.trusted_headers
    config.trusted_proxy_auth = base.trusted_proxy_auth
    config.csp_policy = base.csp_policy
    config.workspaces = {}
    return config


def actor_from_request(request: Request) -> str:
    authenticated_actor = str(getattr(request.state, "lore_actor", "") or "").strip()
    if authenticated_actor:
        return authenticated_actor
    if getattr(request.app.state, "trusted_headers", False):
        header_actor = request.headers.get("X-Lore-Actor", "").strip()
        if header_actor:
            return header_actor
    return "anonymous"


def validate_page_id_input(page_id: str) -> None:
    try:
        sanitize_page_id(page_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def validate_optional_page_id_input(page_id: str | None) -> None:
    if page_id and page_id.strip():
        validate_page_id_input(page_id)


def validate_content(content: str) -> None:
    try:
        sanitize_content(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def validate_optional_content(content: str | None) -> None:
    if content is not None:
        validate_content(content)


def is_rate_limited_write(request: Request) -> bool:
    """Rate-limit write API routes, excluding MCP which enforces its own limits."""
    path = request.url.path
    method = request.method.upper()

    # Only rate-limit mutation methods
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False

    # Rate-limit API mutations. MCP has its own call-aware limiter in routes/mcp.py.
    if path.startswith("/api/"):
        return True

    return False


def client_rate_limit_key(request: Request) -> str:
    if getattr(request.app.state, "trusted_headers", False):
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def current_git_ref() -> str | None:
    global _GIT_REF_CACHE

    now = time.monotonic()
    if _GIT_REF_CACHE is not None:
        ref, cached_at = _GIT_REF_CACHE
        if now - cached_at < GIT_REF_CACHE_TTL_SECONDS:
            return ref

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    ref = result.stdout.strip()
    if not ref:
        return None
    _GIT_REF_CACHE = (ref, now)
    return ref


def index_vectors_for_page(vector_store: VectorStore, page: PageDetail) -> None:
    vector_store.remove_page(page.id)
    for chunk in chunk_page(page.id, page.content, page.body):
        vector_store.upsert_chunk(
            chunk["chunk_id"],
            chunk["page_id"],
            chunk["chunk_index"],
            chunk["content"],
        )
    vector_store.rebuild_doc_freq()


def rebuild_vector_index(repo: LoreRepository, vector_store: VectorStore) -> int:
    vector_store.clear()
    count = 0
    for summary in repo.list_pages():
        page = repo.read_page(summary.id)
        if page is None:
            continue
        for chunk in chunk_page(page.id, page.content, page.body):
            vector_store.upsert_chunk(
                chunk["chunk_id"],
                chunk["page_id"],
                chunk["chunk_index"],
                chunk["content"],
            )
        count += 1
    vector_store.rebuild_doc_freq()
    return count


def retrieve_context(
    repo: LoreRepository,
    search_idx: LoreSearchIndex,
    vector_store: VectorStore,
    graph_cache: LinkGraphCache,
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    graph = graph_cache.get(repo)
    result = hybrid_retrieve(query, search_idx, vector_store, graph, limit=limit)
    return enrich_rag_results(repo, result)


def enrich_rag_results(repo: LoreRepository, result: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    rows = []
    for item in result.get("results", []):
        row = dict(item)
        page = repo.read_page(str(row.get("page_id") or ""))
        if page is not None:
            row["title"] = page.title
            row["kind"] = page.kind
            row["visibility"] = page.visibility
            if not row.get("citations"):
                row["citations"] = [page.body[:200]]
        rows.append(row)
    enriched["results"] = rows
    enriched["total"] = len(rows)
    return enriched


def enrich_expanded_results(repo: LoreRepository, result: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    rows = []
    for item in result.get("results", []):
        row = dict(item)
        page = repo.read_page(str(row.get("page_id") or ""))
        if page is not None:
            row["title"] = page.title
        rows.append(row)
    enriched["results"] = rows
    enriched["total"] = len(rows)
    return enriched


def record_audit(
    request: Request,
    audit_log: AuditLog,
    *,
    operation: str,
    page_id: str,
    summary: str,
    diff_size: int | None = None,
) -> None:
    audit_log.record(
        new_audit_entry(
            actor=actor_from_request(request),
            operation=operation,
            page_id=page_id,
            summary=summary,
            commit_ref=current_git_ref(),
            diff_size=diff_size,
        )
    )


def backlink_groups(links: PageLinks | None, pages: list[PageSummary]) -> list[dict[str, Any]]:
    if links is None:
        return []
    kind_by_id = {page.id: page.kind for page in pages}
    grouped: dict[str, list[LinkEdge]] = {}
    for edge in links.backlinks:
        grouped.setdefault(kind_by_id.get(edge.source, "page"), []).append(edge)
    return [{"kind": kind, "edges": grouped[kind]} for kind in sorted(grouped)]
