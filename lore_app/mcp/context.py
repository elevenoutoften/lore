from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpContext:
    """Typed context object passed to MCP tool handlers."""

    repo: Any
    params: dict[str, Any]
    search_index: Any | None = None
    graph_cache: Any | None = None
    vector_store: Any | None = None
    code_inventories: dict[str, dict[str, Any]] = field(default_factory=dict)
    config: Any | None = None
    ledger_db: Any | None = None
    patch_planner: Any | None = None
    consolidation_worker: Any | None = None
    audit_log: Any | None = None
    metrics: Any | None = None
    request: Any | None = None
