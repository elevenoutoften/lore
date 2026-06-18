from __future__ import annotations

import hashlib
import logging
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from .audit import AuditLog, new_audit_entry
from .config import LOOPBACK_HOSTS, LoreConfig, WorkspaceConfig
from .rag.chunker import chunk_page
from .rag.hybrid_retrieval import hybrid_retrieve
from .repository import InvalidPageId, LoreRepository, build_candidate_page_index, page_result_provenance
from .security import sanitize_content, sanitize_page_id

if TYPE_CHECKING:
    from .link_graph import LinkGraphCache
    from .rag.vector_store import VectorStore
    from .schemas import CaptureRequest, LinkEdge, PageDetail, PageLinks, PageSummary
    from .search_index import LoreSearchIndex

GIT_REF_CACHE_TTL_SECONDS = 300
_GIT_REF_CACHE: tuple[str, float] | None = None
logger = logging.getLogger("lore")


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


def stamp_capture_actor(payload: CaptureRequest, request: Request) -> CaptureRequest:
    """Return a capture payload attributed to the server-resolved caller."""
    actor = actor_from_request(request)
    return payload.model_copy(update={"actor": actor, "agent": actor})


def recall_actor_scope(
    request: Request,
    *,
    requested_actor: str | None,
    cross_actor: bool = False,
) -> str | None:
    """Resolve the actor filter that must be applied to recall queries.

    Authenticated modes are tenant-scoped by default. In auth_mode='none', Lore is
    a single shared local tenant, so caller-supplied actor filters keep their old
    meaning and no mandatory actor scope is applied.
    """
    config = getattr(request.app.state, "config", None)
    if getattr(config, "auth_mode", "none") == "none":
        return requested_actor

    caller_actor = actor_from_request(request)
    caller_role = str(getattr(request.state, "lore_role", "") or "").strip()
    requested = str(requested_actor or "").strip() or None

    if cross_actor:
        if caller_role != "admin":
            raise PermissionError("Admin role is required for cross-actor recall.")
        return requested

    if requested and requested != caller_actor:
        raise PermissionError("Cross-actor recall requires admin role and cross_actor=true.")
    return caller_actor


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
    return bool(path.startswith("/api/"))


def none_mode_local_operator(request: Request) -> bool:
    """Whether to treat the caller as the trusted local operator under auth_mode='none'.

    The default zero-config install runs auth_mode='none' bound loopback-only, so the
    local operator is the only possible caller and the keys/settings UI is open with no
    token. But auth='none' on a non-loopback bind is reachable via LORE_ALLOW_INSECURE_BIND
    (the startup guard only warns), and there the open bypass would let any network client
    mint admin keys or rewrite the LLM settings. So when the server is NOT loopback-bound,
    require the request itself to originate from loopback before granting the bypass.
    """
    config = getattr(request.app.state, "config", None)
    if getattr(config, "auth_mode", None) != "none":
        return False
    if getattr(config, "host", None) in LOOPBACK_HOSTS:
        return True
    client_host = request.client.host if request.client else None
    return client_host in LOOPBACK_HOSTS


def client_rate_limit_key(request: Request) -> str:
    # Give distinct agents independent write budgets: key on a stable agent
    # identity where available, so many agents behind one proxy don't share (and
    # exhaust) a single IP-keyed bucket. Falls back to client IP for anonymous
    # traffic. Runs before AuthMiddleware sets lore_actor for token auth, so the
    # bearer token (hashed, never logged) is used as the per-agent key there.
    actor = str(getattr(request.state, "lore_actor", "") or "").strip()
    if actor:
        return f"actor:{actor}"
    agent_header = request.headers.get("X-Lore-Agent", "").strip() or request.headers.get("X-Lore-Actor", "").strip()
    if agent_header:
        return f"agent:{agent_header}"
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token:
            return "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
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
    chunks = list(chunk_page(page.id, page.content, page.body))
    actor = _optional_frontmatter_string(page.frontmatter.get("actor"))
    lane = _optional_frontmatter_string(page.frontmatter.get("lane"))
    for chunk in chunks:
        chunk["actor"] = actor
        chunk["lane"] = lane
    try:
        vector_store.upsert_page_chunks(page.id, chunks, actor=actor, lane=lane)
    except Exception as exc:
        logger.exception("Vector indexing failed for %s; queued for reconciliation.", page.id)
        if hasattr(vector_store, "record_pending_reindex"):
            vector_store.record_pending_reindex(page.id, str(exc))
        raise


def rebuild_vector_index(repo: LoreRepository, vector_store: VectorStore) -> int:
    vector_store.clear()
    count = 0
    for summary in repo.list_pages():
        page = repo.read_page(summary.id)
        if page is None:
            continue
        index_vectors_for_page(vector_store, page)
        count += 1
    if hasattr(vector_store, "clear_pending_reindex"):
        vector_store.clear_pending_reindex()
    return count


def vector_index_stats(repo: LoreRepository, vector_store: VectorStore) -> dict[str, int]:
    pages = repo.list_pages()
    page_count = len(pages)
    indexed_pages = vector_store.indexed_page_count() if hasattr(vector_store, "indexed_page_count") else 0
    chunk_count = vector_store.chunk_count() if hasattr(vector_store, "chunk_count") else 0
    pending = vector_store.pending_reindex_count() if hasattr(vector_store, "pending_reindex_count") else 0
    scoped_chunks = vector_store.scoped_chunk_count() if hasattr(vector_store, "scoped_chunk_count") else 0
    scope_drift = 0
    if hasattr(vector_store, "page_scopes"):
        indexed_scopes = vector_store.page_scopes()
        for summary in pages:
            page = repo.read_page(summary.id)
            if page is None:
                continue
            expected_actor = _optional_frontmatter_string(page.frontmatter.get("actor"))
            expected_lane = _optional_frontmatter_string(page.frontmatter.get("lane"))
            if expected_actor is None and expected_lane is None:
                continue
            if indexed_scopes.get(summary.id) != (expected_actor, expected_lane):
                scope_drift += 1
    return {
        "page_count": page_count,
        "indexed_pages": indexed_pages,
        "chunk_count": chunk_count,
        "page_drift": page_count - indexed_pages,
        "pending_reindex": pending,
        "scoped_chunks": scoped_chunks,
        "scope_drift": scope_drift,
    }


def reconcile_vector_index(
    repo: LoreRepository,
    vector_store: VectorStore,
    *,
    force: bool = False,
) -> dict[str, int | bool]:
    before = vector_index_stats(repo, vector_store)
    should_rebuild = force or before["page_drift"] != 0 or before["pending_reindex"] > 0 or before["scope_drift"] > 0
    indexed = 0
    if should_rebuild:
        indexed = rebuild_vector_index(repo, vector_store)
    after = vector_index_stats(repo, vector_store)
    return {**after, "reconciled": should_rebuild, "rebuilt_pages": indexed}


def update_vector_index_metrics(metrics: Any, stats: dict[str, int | bool]) -> None:
    if metrics is None or not hasattr(metrics, "set_vector_index_metrics"):
        return
    metrics.set_vector_index_metrics(
        chunk_count=int(stats.get("chunk_count", 0)),
        indexed_pages=int(stats.get("indexed_pages", 0)),
        page_drift=int(stats.get("page_drift", 0)),
        pending_reindex=int(stats.get("pending_reindex", 0)),
        scope_drift=int(stats.get("scope_drift", 0)),
    )


def _optional_frontmatter_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


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


def enrich_expanded_results(
    repo: LoreRepository,
    result: dict[str, Any],
    ledger: Any | None = None,
    *,
    actor: str | None = None,
) -> dict[str, Any]:
    enriched = dict(result)
    rows = []
    ledger_candidates_by_page = build_candidate_page_index(ledger, actor=actor)
    for item in result.get("results", []):
        row = dict(item)
        page = repo.read_page(str(row.get("page_id") or ""))
        if page is not None:
            row["title"] = page.title
            row.update(page_result_provenance(page, ledger_candidates_by_page=ledger_candidates_by_page))
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


def value_error_to_http(exc: ValueError) -> HTTPException:
    """Map ledger/planner ValueErrors to 404 (missing) or 409 (conflict), not a raw 500."""
    detail = str(exc)
    code = 404 if "not found" in detail.lower() else 409
    return HTTPException(status_code=code, detail=detail)
