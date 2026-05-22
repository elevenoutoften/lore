from __future__ import annotations

from .admin import router as admin_router
from .api_keys import router as api_keys_router
from .captures import router as captures_router
from .consolidation import consolidation_router
from .heartbeat import router as heartbeat_router
from .distillation import router as distillation_router
from .extraction import router as extraction_router
from .graph import router as graph_router
from .lint import router as lint_router
from .mcp import router as mcp_router
from .memory import router as memory_router
from .metadata import router as metadata_router
from .pages import router as pages_router
from .procedures import router as procedures_router
from .rag import router as rag_router
from .ledger import ledger_router
from .search import router as search_router
from .traces import router as trace_router

__all__ = [
    "admin_router",
    "api_keys_router",
    "captures_router",
    "consolidation_router",
    "distillation_router",
    "extraction_router",
    "graph_router",
    "heartbeat_router",
    "ledger_router",
    "lint_router",
    "mcp_router",
    "memory_router",
    "metadata_router",
    "pages_router",
    "procedures_router",
    "rag_router",
    "search_router",
    "trace_router",
]
