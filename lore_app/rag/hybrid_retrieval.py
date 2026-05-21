"""Hybrid retrieval combining BM25, TF-IDF vector search, and graph expansion."""
from __future__ import annotations

from typing import Any


def hybrid_retrieve(
    query: str,
    fts_index: Any,
    vector_store: Any,
    graph_cache: Any = None,
    limit: int = 10,
    fts_weight: float = 0.5,
    vector_weight: float = 0.3,
    graph_weight: float = 0.2,
    lane: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Run hybrid retrieval combining BM25, vector, and graph signals.

    Args:
        lane: Filter to a specific retrieval lane (project, procedural, ops, companion, draft).
        actor: Filter to captures produced by a specific agent.
    """
    page_scores: dict[str, dict[str, Any]] = {}

    # Pass lane/actor to FTS search if supported
    search_kwargs: dict[str, Any] = {"limit": limit * 2}
    if lane:
        search_kwargs["lane"] = lane
    if actor:
        search_kwargs["actor"] = actor
    fts_results = fts_index.search(query, **search_kwargs) if hasattr(fts_index, "search") else []
    for result in fts_results:
        page_id = result.get("page_id", "")
        score = float(result.get("score", result.get("rank", 0.0)) or 0.0)
        if not page_id:
            continue
        page_scores[page_id] = {
            "page_id": page_id,
            "score": score * fts_weight,
            "sources": ["fts"],
            "citations": [result.get("snippet", "")],
        }

    vec_results = vector_store.search(query, limit=limit * 2) if hasattr(vector_store, "search") else []
    for result in vec_results:
        page_id = result.get("page_id", "")
        score = float(result.get("score", 0.0) or 0.0)
        if not page_id:
            continue
        if lane and result.get("lane") and result.get("lane") != lane:
            continue
        if actor and result.get("actor") and result.get("actor") != actor:
            continue
        if page_id in page_scores:
            page_scores[page_id]["score"] += score * vector_weight
            page_scores[page_id]["sources"].append("vector")
            citation = str(result.get("content", ""))[:200]
            if citation:
                page_scores[page_id]["citations"].append(citation)
        else:
            page_scores[page_id] = {
                "page_id": page_id,
                "score": score * vector_weight,
                "sources": ["vector"],
                "citations": [str(result.get("content", ""))[:200]],
            }

    graph = _resolve_graph(graph_cache)
    if graph is not None:
        linked_pages: dict[str, float] = {}
        for page_id, info in page_scores.items():
            if info["score"] <= 0:
                continue
            for edge in getattr(graph, "links", []):
                if edge.source == page_id and edge.target and edge.exists and not edge.external:
                    linked_pages[edge.target] = linked_pages.get(edge.target, 0.0) + info["score"] * 0.1
        for page_id, boost in linked_pages.items():
            if page_id in page_scores:
                page_scores[page_id]["score"] += boost * graph_weight
                if "graph" not in page_scores[page_id]["sources"]:
                    page_scores[page_id]["sources"].append("graph")
            else:
                page_scores[page_id] = {
                    "page_id": page_id,
                    "score": boost * graph_weight,
                    "sources": ["graph"],
                    "citations": [],
                }

    results = sorted(page_scores.values(), key=lambda item: item["score"], reverse=True)[:limit]
    return {"query": query, "total": len(results), "results": results}


def _resolve_graph(graph_cache: Any) -> Any | None:
    if graph_cache is None:
        return None
    if hasattr(graph_cache, "links"):
        return graph_cache
    try:
        return graph_cache.get(None)
    except Exception:
        return None
