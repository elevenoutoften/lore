from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api_keys import LoreApiKeyStore
from .auth import AuthMiddleware
from .config import LoreConfig, VALID_AUTH_MODES
from .consolidation_worker import ConsolidationWorker
from .context_graph import ContextGraphCache
from .ledger import LedgerDB
from .link_graph import LinkGraphCache
from .lint_config import LintConfig
from .llm_provider import build_llm_client
from .observability import MetricsCollector, log_request
from .patch_planner import PatchPlanner
from .policy_engine import PolicyEngine
from .rag.vector_store import VectorStore
from .repository import LoreRepository
from .route_utils import actor_from_request, client_rate_limit_key, is_rate_limited_write, retrieve_context, workspace_lore_config
from .routes import admin_router, api_keys_router, captures_router, consolidation_router, context_graph_router, distillation_router, extraction_router, graph_router, heartbeat_router, ledger_router, lint_router, mcp_router, memory_router, metadata_router, pages_router, policies_router, precedents_router, procedures_router, provenance_router, rag_router, search_router, trace_router
from .routes.admin import package_version
from .search_index import LoreSearchIndex
from .security import RateLimiter
from .audit import AuditLog

PACKAGE_DIR = Path(__file__).resolve().parent


def default_content_dir() -> Path:
    return LoreConfig().content_dir


def create_app(
    config: LoreConfig | None = None,
    mount_workspaces: bool = True,
) -> FastAPI:
    lore_config = config or LoreConfig()

    if lore_config.auth_mode not in VALID_AUTH_MODES:
        raise ValueError(
            f"Unsupported LORE_AUTH_MODE={lore_config.auth_mode!r}. "
            f"Must be one of: {', '.join(VALID_AUTH_MODES)}."
        )

    # Fail closed: refuse to start if auth_mode='none' and binding a non-loopback address
    if lore_config.auth_mode == "none" and lore_config.host not in ("127.0.0.1", "localhost", "::1"):
        if not lore_config.allow_insecure_bind:
            raise ValueError(
                "SECURITY: Lore is configured with LORE_AUTH_MODE=none but binds to a "
                f"non-loopback address ({lore_config.host}). This exposes the API without "
                "authentication. Either set LORE_AUTH_MODE to 'bearer', 'basic', or 'api_key'; "
                "bind to 127.0.0.1; or set LORE_ALLOW_INSECURE_BIND=true to acknowledge "
                "the risk and proceed."
            )
        import logging
        logging.getLogger("lore").warning(
            "SECURITY: Lore is running with LORE_AUTH_MODE=none on non-loopback "
            f"address {lore_config.host}. This is insecure unless an external gateway "
            "enforces authentication."
        )

    search_idx = LoreSearchIndex(lore_config.search_db)
    repo = LoreRepository(lore_config.content_dir, search_index=search_idx)
    repo.ensure_root()
    vector_store = VectorStore(lore_config.vector_db)
    lint_config = LintConfig(Path(lore_config.content_dir) / ".lore-lint.json")
    graph_cache = LinkGraphCache()
    context_graph_cache = ContextGraphCache()
    ledger_db = LedgerDB(lore_config.ledger_db)
    ledger_db.initialize()
    policy_engine = PolicyEngine(ledger_db)
    api_key_store = LoreApiKeyStore(lore_config.api_keys_db)
    api_key_store.initialize()
    audit_log = AuditLog(Path(lore_config.content_dir) / ".lore" / "audit", retention_days=lore_config.audit_retention_days)
    metrics = MetricsCollector()
    metrics.set_index_size(len(repo.list_pages()))

    app = FastAPI(title=lore_config.app_name, description=lore_config.app_description, version=package_version())
    app.state.config = lore_config
    app.state.trusted_headers = lore_config.trusted_headers
    app.state.trusted_proxy_auth = lore_config.trusted_proxy_auth
    app.state.repository = repo
    app.state.search_index = search_idx
    app.state.vector_store = vector_store
    app.state.lint_config = lint_config
    app.state.graph_cache = graph_cache
    app.state.context_graph_cache = context_graph_cache
    app.state.ledger_db = ledger_db
    app.state.policy_engine = policy_engine
    app.state.api_key_store = api_key_store
    app.state.audit_log = audit_log
    app.state.patch_planner = PatchPlanner(repo, ledger_db, audit_log, policy_engine=policy_engine)
    app.state.consolidation_worker = ConsolidationWorker(
        repo,
        ledger_db,
        app.state.patch_planner,
        lore_config,
        audit_log,
    )
    app.state.metrics = metrics
    app.state.templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    app.state.code_inventories = {}
    app.state.llm_client = build_llm_client(config=lore_config)
    app.state.write_rate_limiter = RateLimiter(
        max_requests=lore_config.write_rate_limit,
        window_seconds=lore_config.write_rate_window_seconds,
    )
    app.state.retrieve_context = lambda query, limit=10: retrieve_context(repo, search_idx, vector_store, graph_cache, query, limit)

    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    if lore_config.auth_mode in ("bearer", "basic"):
        if not lore_config.auth_secret or not lore_config.auth_secret.strip():
            raise ValueError(
                "LORE_AUTH_SECRET must be a non-empty string when "
                f"LORE_AUTH_MODE={lore_config.auth_mode!r}. "
                f"Set a strong secret or switch to LORE_AUTH_MODE=none or LORE_AUTH_MODE=api_key."
            )

    if lore_config.auth_mode != "none":
        app.add_middleware(
            AuthMiddleware,
            mode=lore_config.auth_mode,
            secret=lore_config.auth_secret,
            api_key_store=api_key_store,
            trusted_proxy_auth=lore_config.trusted_proxy_auth,
        )

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            actor = actor_from_request(request)
            path = request.url.path
            log_request(request.method, path, status_code, duration_ms, actor)
            metrics.record_request(path, request.method, status_code, duration_ms)

    @app.middleware("http")
    async def write_rate_limit_middleware(request: Request, call_next):
        if is_rate_limited_write(request):
            key = client_rate_limit_key(request)
            if not app.state.write_rate_limiter.check(key):
                return JSONResponse({"detail": "Rate limit exceeded."}, status_code=429)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.csp_nonce = secrets.token_urlsafe(16)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = lore_config.csp_policy or (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{request.state.csp_nonce}'"
        )
        return response

    @app.on_event("shutdown")
    def close_db_connections() -> None:
        search_idx.close()
        vector_store.close()
        ledger_db.close()
        api_key_store.close()
        app.state.llm_client.close()

    if mount_workspaces:
        for workspace_name, workspace in lore_config.workspaces.items():
            app.mount(
                f"/{workspace_name}",
                create_app(workspace_lore_config(lore_config, workspace), mount_workspaces=False),
                name=f"workspace:{workspace_name}",
            )

    app.include_router(admin_router)
    app.include_router(api_keys_router)
    app.include_router(metadata_router)
    app.include_router(captures_router)
    app.include_router(distillation_router)
    app.include_router(extraction_router)
    app.include_router(ledger_router)
    app.include_router(consolidation_router)
    app.include_router(memory_router)
    app.include_router(search_router)
    app.include_router(trace_router)
    app.include_router(rag_router)
    app.include_router(lint_router)
    app.include_router(heartbeat_router)
    app.include_router(graph_router)
    app.include_router(context_graph_router)
    app.include_router(mcp_router)
    app.include_router(policies_router)
    app.include_router(precedents_router)
    app.include_router(procedures_router)
    app.include_router(provenance_router)
    app.include_router(pages_router)

    return app
