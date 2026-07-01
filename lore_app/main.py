from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api_keys import LoreApiKeyStore
from .audit import AuditLog
from .auth import AuthMiddleware
from .config import VALID_AUTH_MODES, LoreConfig, merged_llm_config, merged_maintenance_config
from .consolidation_worker import ConsolidationWorker
from .context_graph import ContextGraphCache
from .heartbeat import emit_heartbeat_captures
from .ledger import LedgerDB
from .link_graph import LinkGraphCache
from .lint_config import LintConfig
from .llm_provider import build_embedding_client_from_config, build_llm_client_from_config
from .observability import MetricsCollector, log_request
from .patch_planner import PatchPlanner
from .policy_engine import PolicyEngine
from .rag.vector_store import VectorStore
from .repository import LoreRepository
from .route_utils import (
    actor_from_request,
    client_rate_limit_key,
    index_vectors_for_page,
    is_rate_limited_write,
    reconcile_vector_index,
    retrieve_context,
    update_vector_index_metrics,
    vector_index_stats,
    workspace_lore_config,
)
from .routes import (
    admin_router,
    api_keys_router,
    captures_router,
    consolidation_router,
    context_graph_router,
    distillation_router,
    extraction_router,
    graph_router,
    heartbeat_router,
    ledger_router,
    lint_router,
    mcp_router,
    memory_router,
    metadata_router,
    pages_router,
    policies_router,
    precedents_router,
    procedures_router,
    provenance_router,
    rag_router,
    search_router,
    settings_router,
    trace_router,
)
from .routes.admin import package_version
from .search_index import LoreSearchIndex
from .security import RateLimiter
from .settings_store import SettingsStore

PACKAGE_DIR = Path(__file__).resolve().parent


def rebuild_llm_client(app: FastAPI) -> None:
    """Rebuild app.state.llm_client from merged config and runtime settings."""

    if not hasattr(app.state, "_llm_lock"):
        app.state._llm_lock = threading.Lock()
    with app.state._llm_lock:
        old_client = app.state.llm_client
        provider_config = merged_llm_config(app.state.config, app.state.settings_store)
        app.state.llm_client = build_llm_client_from_config(provider_config)
        rebuild_dense = app.state.vector_store.configure_embedding_backend(
            build_embedding_client_from_config(provider_config)
        )
        app.state.ledger_db.configure_semantic_scorer(
            app.state.vector_store.semantic_similarities if app.state.vector_store.dense_enabled else None
        )
        if rebuild_dense:
            stats = reconcile_vector_index(app.state.repository, app.state.vector_store, force=True)
            update_vector_index_metrics(getattr(app.state, "metrics", None), stats)
        old_client.close()


def _start_vector_reconciler(
    *,
    repo: LoreRepository,
    vector_store: VectorStore,
    metrics: MetricsCollector,
    interval_seconds: int,
    stop_event: threading.Event,
) -> threading.Thread | None:
    if interval_seconds <= 0:
        return None

    def reconcile_loop() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                stats = reconcile_vector_index(repo, vector_store)
                update_vector_index_metrics(metrics, stats)
                if stats.get("reconciled"):
                    logging.getLogger("lore").info("Periodic vector index reconciliation completed: %s", stats)
            except Exception:  # pragma: no cover - defensive background reconciliation
                logging.getLogger("lore").exception("Periodic vector index reconciliation failed.")

    thread = threading.Thread(target=reconcile_loop, name="lore-vector-reconciler", daemon=True)
    thread.start()
    return thread


def run_maintenance_tick(app: FastAPI) -> None:
    """Run one maintenance pass: heartbeat self-audit, ledger decay, distillation.

    Runs under the existing auto-consolidation coalescing lock so it never mutates
    the ledger/pages concurrently with a capture-triggered auto-consolidation. Each
    sub-step is best-effort (a failure in one does not abort the others).
    """
    state = app.state
    logger = logging.getLogger("lore")
    lock = getattr(state, "auto_consolidate_lock", None)
    if lock is not None:
        lock.acquire()
    try:
        repo = state.repository
        ledger = state.ledger_db

        try:
            cleanup = ledger.cleanup_disposable_candidates()
            cleaned = sum(len(ids) for ids in cleanup.values())
            if cleaned:
                logger.info("Maintenance cleaned disposable extraction candidates: %s", cleanup)
                state.context_graph_cache.invalidate()
        except Exception:  # pragma: no cover - best-effort maintenance step
            logger.exception("Maintenance disposable-candidate cleanup step failed.")

        try:
            captures = emit_heartbeat_captures(repo, config=state.lint_config, graph=state.graph_cache.get(repo))
            for capture in captures:
                state.metrics.increment_index_size()
                state.search_index.upsert_page_from_detail(capture)
                index_vectors_for_page(state.vector_store, capture)
                state.graph_cache.invalidate()
                state.context_graph_cache.invalidate()
        except Exception:  # pragma: no cover - best-effort maintenance step
            logger.exception("Maintenance heartbeat-capture step failed.")

        try:
            ledger.apply_decay()
        except Exception:  # pragma: no cover - best-effort maintenance step
            logger.exception("Maintenance ledger-decay step failed.")

        try:
            from .distillation import distill_daily, get_pending_days
            from .schemas import DailyDistillRequest

            llm_client = getattr(state, "llm_client", None)
            for day in get_pending_days(repo).pending_days:
                distill_daily(repo, DailyDistillRequest(date=day.date), llm_client=llm_client)
        except Exception:  # pragma: no cover - best-effort maintenance step
            logger.exception("Maintenance distillation step failed.")

        state.last_maintenance_at = datetime.now(UTC).isoformat()
    finally:
        if lock is not None:
            lock.release()


def _start_maintenance_scheduler(
    *,
    app: FastAPI,
    interval_seconds: int,
    enabled: bool,
    stop_event: threading.Event,
) -> threading.Thread | None:
    if not enabled or interval_seconds <= 0:
        return None

    def maintenance_loop() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                run_maintenance_tick(app)
            except Exception:  # pragma: no cover - defensive background maintenance
                logging.getLogger("lore").exception("Periodic maintenance tick failed.")

    thread = threading.Thread(target=maintenance_loop, name="lore-maintenance", daemon=True)
    thread.start()
    return thread


def restart_maintenance_scheduler(app: FastAPI) -> None:
    """(Re)start the maintenance scheduler from merged config + runtime settings.

    The hot-reload hook behind PUT/DELETE /api/settings/maintenance: it swaps the
    background thread so a runtime toggle takes effect without a process restart.

    Lock ordering is deliberate. ``run_maintenance_tick`` holds
    ``auto_consolidate_lock`` for the whole tick (distillation can take minutes), so we
    only do the O(1) handle swap under ``_maintenance_lock`` and then join the retired
    thread *after* releasing it. Joining a mid-tick thread while holding
    ``_maintenance_lock`` would stall the settings request. The two locks are never
    nested, so they cannot deadlock.
    """
    if not hasattr(app.state, "_maintenance_lock"):
        app.state._maintenance_lock = threading.Lock()

    old_thread: threading.Thread | None
    with app.state._maintenance_lock:
        old_stop = getattr(app.state, "maintenance_stop_event", None)
        old_thread = getattr(app.state, "maintenance_thread", None)
        if old_stop is not None:
            old_stop.set()

        enabled, interval_seconds = merged_maintenance_config(app.state.config, app.state.settings_store)
        stop_event = threading.Event()
        thread = _start_maintenance_scheduler(
            app=app,
            interval_seconds=interval_seconds,
            enabled=enabled,
            stop_event=stop_event,
        )
        app.state.maintenance_stop_event = stop_event if thread is not None else None
        app.state.maintenance_thread = thread

    # Best-effort reaping of the retired thread, outside the lock (see docstring). A
    # daemon thread that is mid-tick will exit at the next stop-event check anyway.
    if old_thread is not None and old_thread.is_alive():
        old_thread.join(timeout=5)


def create_app(
    config: LoreConfig | None = None,
    mount_workspaces: bool = True,
) -> FastAPI:
    lore_config = config or LoreConfig()

    if lore_config.auth_mode not in VALID_AUTH_MODES:
        raise ValueError(
            f"Unsupported LORE_AUTH_MODE={lore_config.auth_mode!r}. Must be one of: {', '.join(VALID_AUTH_MODES)}."
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
    settings_store = SettingsStore(lore_config.settings_db)
    settings_store.initialize()
    provider_config = merged_llm_config(lore_config, settings_store)
    dense_rebuild_required = vector_store.configure_embedding_backend(
        build_embedding_client_from_config(provider_config)
    )
    ledger_db.configure_semantic_scorer(vector_store.semantic_similarities if vector_store.dense_enabled else None)
    audit_log = AuditLog(
        Path(lore_config.content_dir) / ".lore" / "audit", retention_days=lore_config.audit_retention_days
    )
    metrics = MetricsCollector()
    metrics.set_index_size(len(repo.list_pages()))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Cold-start: build the search indexes for an existing vault so search and
        # RAG work out of the box. A fresh process pointed at an on-disk vault starts
        # with empty FTS + vector tables (both are otherwise only populated by
        # page-write triggers), so the browser /search, the FTS/BM25 endpoints, and
        # lore_rag_context would all return nothing until a manual /api/search/reindex
        # that no onboarding doc mentions. Guarded by emptiness so restarts are cheap.
        try:
            page_count = len(repo.list_pages())
        except Exception:  # pragma: no cover - defensive
            page_count = 0
        if page_count > 0:
            try:
                if not search_idx.has_pages():
                    indexed = search_idx.rebuild(repo)
                    logging.getLogger("lore").info("Built full-text index for %d page(s) on startup.", indexed)
            except Exception:  # pragma: no cover - best-effort startup index
                logging.getLogger("lore").exception("Startup full-text index build failed; run /api/search/reindex.")
            try:
                stats = vector_index_stats(repo, vector_store)
                update_vector_index_metrics(metrics, stats)
                if (
                    vector_store.chunk_count() == 0
                    or dense_rebuild_required
                    or stats["page_drift"] != 0
                    or stats["scope_drift"] > 0
                    or stats["pending_reindex"] > 0
                ):
                    # The vector build can be expensive, so it is capped; very large
                    # vaults must opt in with /api/search/reindex. Warn loudly rather
                    # than skip silently, or lore_rag_context returns 0 with no clue why.
                    if page_count <= 5000:
                        stats = reconcile_vector_index(repo, vector_store, force=dense_rebuild_required)
                        update_vector_index_metrics(metrics, stats)
                        logging.getLogger("lore").info(
                            "Reconciled vector index on startup: %s",
                            stats,
                        )
                    else:
                        logging.getLogger("lore").warning(
                            "Vault has %d pages (over the %d-page startup ceiling); the vector index "
                            "was NOT auto-built. RAG/semantic search returns nothing until you run "
                            "POST /api/search/reindex.",
                            page_count,
                            5000,
                        )
            except Exception:  # pragma: no cover - best-effort startup index
                logging.getLogger("lore").exception("Startup vector index build failed; run /api/search/reindex.")
        else:
            update_vector_index_metrics(metrics, vector_index_stats(repo, vector_store))
        stop_reconcile = threading.Event()
        reconcile_thread = _start_vector_reconciler(
            repo=repo,
            vector_store=vector_store,
            metrics=metrics,
            interval_seconds=lore_config.vector_reconcile_interval_seconds,
            stop_event=stop_reconcile,
        )
        # Start the maintenance scheduler via the runtime hook so its thread handle
        # lives on app.state and the /settings toggle can stop/replace it later.
        restart_maintenance_scheduler(app)
        yield
        stop_reconcile.set()
        if reconcile_thread is not None:
            reconcile_thread.join(timeout=2)
        stop_maintenance = getattr(app.state, "maintenance_stop_event", None)
        maintenance_thread = getattr(app.state, "maintenance_thread", None)
        if stop_maintenance is not None:
            stop_maintenance.set()
        if maintenance_thread is not None:
            maintenance_thread.join(timeout=2)
        search_idx.close()
        vector_store.close()
        ledger_db.close()
        api_key_store.close()
        settings_store.close()
        app.state.llm_client.close()

    app = FastAPI(
        title=lore_config.app_name,
        description=lore_config.app_description,
        version=package_version(),
        lifespan=lifespan,
    )
    app.state.config = lore_config
    # Per-process token for cache-busting the SPA static assets (?v=...). Changes on
    # every restart/deploy so a browser never serves a stale lore2 app.js/css.
    app.state.asset_version = f"{package_version()}.{secrets.token_hex(4)}"
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
    app.state.settings_store = settings_store
    app.state.audit_log = audit_log
    app.state.patch_planner = PatchPlanner(repo, ledger_db, audit_log, policy_engine=policy_engine)
    app.state.consolidation_worker = ConsolidationWorker(
        repo,
        ledger_db,
        app.state.patch_planner,
        lore_config,
        audit_log,
        # Resolve the live, hot-reloadable client per run so auto-consolidation
        # and the scheduled worker use the configured LLM extractor.
        llm_client_provider=lambda: getattr(app.state, "llm_client", None),
        metrics=metrics,
    )
    # Coalescing guard for background auto-consolidation triggered off captures.
    app.state.auto_consolidate_lock = threading.Lock()
    app.state.auto_consolidate_rerun = False
    app.state.last_maintenance_at = None
    # Maintenance scheduler handles, managed by restart_maintenance_scheduler() so the
    # /settings toggle can start/stop the thread at runtime (see L-CFG-01 precedent).
    app.state._maintenance_lock = threading.Lock()
    app.state.maintenance_stop_event = None
    app.state.maintenance_thread = None
    app.state.metrics = metrics
    app.state.templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    app.state.code_inventories = {}
    app.state.llm_client = build_llm_client_from_config(provider_config)
    app.state.write_rate_limiter = RateLimiter(
        max_requests=lore_config.write_rate_limit,
        window_seconds=lore_config.write_rate_window_seconds,
    )
    app.state.retrieve_context = lambda query, limit=10: retrieve_context(
        repo, search_idx, vector_store, graph_cache, query, limit
    )

    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    if lore_config.auth_mode in ("bearer", "basic") and (
        not lore_config.auth_secret or not lore_config.auth_secret.strip()
    ):
        raise ValueError(
            "LORE_AUTH_SECRET must be a non-empty string when "
            f"LORE_AUTH_MODE={lore_config.auth_mode!r}. "
            f"Set a strong secret or switch to LORE_AUTH_MODE=none or LORE_AUTH_MODE=api_key."
        )

    # Browser session-cookie signing secret: prefer an explicit secret, then the
    # auth secret, else a per-process random key (cookies reset on restart).
    session_secret = lore_config.session_secret or lore_config.auth_secret or secrets.token_urlsafe(32)
    app.state.session_secret = session_secret

    if lore_config.auth_mode != "none":
        if (
            lore_config.trusted_proxy_auth
            and not lore_config.trusted_proxy_secret
            and not lore_config.trusted_proxy_cidrs
        ):
            logging.getLogger("lore").warning(
                "LORE_TRUSTED_PROXY_AUTH is enabled but neither LORE_TRUSTED_PROXY_SECRET "
                "nor LORE_TRUSTED_PROXY_CIDRS is set; trusting reverse-proxy identity "
                "headers only from loopback/private source ranges (127.0.0.0/8, RFC1918, "
                "link-local). Public source IPs are rejected. If your proxy reaches Lore "
                "from a public IP, or to harden trust, set LORE_TRUSTED_PROXY_SECRET (proxy "
                "sends a matching X-Lore-Proxy-Secret) or LORE_TRUSTED_PROXY_CIDRS."
            )
        app.add_middleware(
            AuthMiddleware,
            mode=lore_config.auth_mode,
            secret=lore_config.auth_secret,
            api_key_store=api_key_store,
            trusted_proxy_auth=lore_config.trusted_proxy_auth,
            trusted_proxy_cidrs=lore_config.trusted_proxy_cidrs,
            trusted_proxy_secret=lore_config.trusted_proxy_secret,
            session_secret=session_secret,
            metrics_public=lore_config.metrics_public,
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
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        csp = lore_config.csp_policy or (
            f"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-{request.state.csp_nonce}'"
        )
        # The /embed widget is designed to load inside an iframe; X-Frame-Options:
        # DENY would block it (even same-origin). Relax it for /embed only and use
        # CSP frame-ancestors, which also expresses cross-origin embedders.
        path = request.url.path
        if path == "/embed" or path.endswith("/embed"):
            ancestors = lore_config.embed_frame_ancestors
            if ancestors:
                # Cross-origin embedding: rely on frame-ancestors (XFO has no allowlist).
                csp = f"{csp}; frame-ancestors 'self' {' '.join(ancestors)}"
            else:
                response.headers["X-Frame-Options"] = "SAMEORIGIN"
                csp = f"{csp}; frame-ancestors 'self'"
        else:
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = csp
        return response

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
    app.include_router(settings_router)
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
