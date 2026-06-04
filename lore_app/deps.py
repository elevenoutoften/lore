from __future__ import annotations

from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any

from fastapi import Request  # noqa: TC002

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from .api_keys import LoreApiKeyStore
    from .audit import AuditLog
    from .config import LoreConfig
    from .consolidation_worker import ConsolidationWorker
    from .context_graph import ContextGraphCache
    from .ledger import LedgerDB
    from .link_graph import LinkGraphCache
    from .lint_config import LintConfig
    from .observability import MetricsCollector
    from .patch_planner import PatchPlanner
    from .rag.vector_store import VectorStore
    from .repository import LoreRepository
    from .search_index import LoreSearchIndex
    from .settings_store import SettingsStore


def get_config(request: Request) -> LoreConfig:
    return request.app.state.config


def get_repo(request: Request) -> LoreRepository:
    return request.app.state.repository


def get_search_index(request: Request) -> LoreSearchIndex:
    return request.app.state.search_index


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


def get_lint_config(request: Request) -> LintConfig:
    return request.app.state.lint_config


def get_graph_cache(request: Request) -> LinkGraphCache:
    return request.app.state.graph_cache


def get_context_graph_cache(request: Request) -> ContextGraphCache:
    return request.app.state.context_graph_cache


def get_audit_log(request: Request) -> AuditLog:
    return request.app.state.audit_log


def get_metrics(request: Request) -> MetricsCollector:
    return request.app.state.metrics


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def get_code_inventories(request: Request) -> dict[str, Any]:
    return request.app.state.code_inventories


def get_retrieve_context(request: Request) -> Callable[[str, int], dict[str, Any]]:
    return request.app.state.retrieve_context


def get_ledger_db(request: Request) -> LedgerDB:
    return request.app.state.ledger_db


def get_patch_planner(request: Request) -> PatchPlanner:
    return request.app.state.patch_planner


def get_consolidation_worker(request: Request) -> ConsolidationWorker:
    return request.app.state.consolidation_worker


def get_api_key_store(request: Request) -> LoreApiKeyStore:
    return request.app.state.api_key_store


def get_settings_store(request: Request) -> SettingsStore:
    return request.app.state.settings_store
