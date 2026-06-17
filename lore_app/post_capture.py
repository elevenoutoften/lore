from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .consolidation_worker import run_auto_consolidation
from .route_utils import index_vectors_for_page

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

    from .context_graph import ContextGraphCache
    from .link_graph import LinkGraphCache
    from .rag.vector_store import VectorStore
    from .schemas import PageDetail
    from .search_index import LoreSearchIndex


def run_post_capture_side_effects(
    *,
    app: Any,
    page: PageDetail,
    search_idx: LoreSearchIndex | None = None,
    vector_store: VectorStore | None = None,
    graph_cache: LinkGraphCache | None = None,
    context_graph_cache: ContextGraphCache | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """Refresh derived state and trigger consolidation after a capture write."""

    if search_idx is not None:
        search_idx.upsert_page_from_detail(page)
    if vector_store is not None:
        if background_tasks is not None:
            background_tasks.add_task(index_vectors_for_page, vector_store, page)
        else:
            index_vectors_for_page(vector_store, page)

    state = getattr(app, "state", None)
    resolved_graph_cache = graph_cache or getattr(state, "graph_cache", None)
    if resolved_graph_cache is not None:
        resolved_graph_cache.invalidate()
    resolved_context_graph_cache = context_graph_cache or getattr(state, "context_graph_cache", None)
    if resolved_context_graph_cache is not None:
        resolved_context_graph_cache.invalidate()

    config = getattr(state, "config", None)
    if not getattr(config, "auto_consolidate", False):
        return
    if background_tasks is not None:
        background_tasks.add_task(run_auto_consolidation, app)
    else:
        run_auto_consolidation(app)
