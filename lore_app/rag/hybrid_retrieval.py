"""Hybrid retrieval combining BM25, TF-IDF vector search, and graph expansion."""

from __future__ import annotations

import json
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


def hybrid_retrieve_expanded(
    query: str,
    fts_index: Any,
    vector_store: Any,
    context_graph: Any,
    ledger: Any = None,
    limit: int = 10,
    expand_hops: int = 2,
    expand_edge_types: list[str] | None = None,
    include_claims: bool = True,
    include_traces: bool = False,
    include_decisions: bool = True,
    fts_weight: float = 0.5,
    vector_weight: float = 0.3,
    graph_weight: float = 0.2,
    lane: str | None = None,
    actor: str | None = None,
    claim_actor: str | None = None,
) -> dict[str, Any]:
    """Run hybrid retrieval with bounded multi-hop context graph expansion."""
    if claim_actor:
        from ..context_graph import scope_context_graph

        context_graph = scope_context_graph(context_graph, claim_actor)
    base_result = hybrid_retrieve(
        query,
        fts_index,
        vector_store,
        None,
        limit=limit * 2,
        fts_weight=fts_weight,
        vector_weight=vector_weight,
        graph_weight=0,
        lane=lane,
        actor=actor,
    )
    base_results = base_result.get("results", [])
    hit_page_ids = {str(result.get("page_id") or "") for result in base_results if result.get("page_id")}
    hit_by_page = {str(result.get("page_id")): dict(result) for result in base_results if result.get("page_id")}

    if context_graph is None or expand_hops <= 0:
        rows = [_expanded_base_row(result, hit_page_ids) for result in base_results[:limit]]
        return {
            "query": query,
            "total": len(rows),
            "results": rows,
            "expansion_stats": {
                "hops": 0 if context_graph is None else expand_hops,
                "nodes_visited": len(hit_page_ids) if context_graph is not None else 0,
                "paths_found": 0,
            },
        }

    scoped_graph = _query_scoped_context_graph(context_graph, hit_page_ids, expand_hops)
    node_index = {node.id: node for node in getattr(scoped_graph, "nodes", [])}
    adjacency = _context_graph_adjacency(scoped_graph, expand_edge_types)

    reachable: dict[str, list[dict[str, Any]]] = {}
    frontier: list[tuple[str, dict[str, Any]]] = []
    visited_depth: dict[str, int] = {}
    nodes_visited = 0
    paths_found = 0

    for page_id in hit_page_ids:
        score = float(hit_by_page.get(page_id, {}).get("score", 0.0) or 0.0)
        seed_path = {"source_id": page_id, "path": [], "hops": 0, "score": score}
        reachable.setdefault(page_id, []).append(seed_path)
        frontier.append((page_id, seed_path))
        visited_depth[page_id] = 0
    nodes_visited = len(visited_depth)

    for _ in range(expand_hops):
        next_frontier: list[tuple[str, dict[str, Any]]] = []
        for node_id, path_info in frontier:
            for neighbor_id, edge, direction in adjacency.get(node_id, []):
                next_hops = int(path_info["hops"]) + 1
                neighbor_node = node_index.get(neighbor_id)
                target_type = getattr(getattr(neighbor_node, "type", None), "value", None) or "page"
                step = {
                    "edge_type": getattr(getattr(edge, "type", None), "value", None) or str(getattr(edge, "type", "")),
                    "target_type": target_type,
                    "target_id": neighbor_id,
                    "direction": direction,
                }
                decay = 0.7 if direction == "outgoing" else 0.5
                next_path = {
                    "source_id": path_info["source_id"],
                    "path": [*path_info["path"], step],
                    "hops": next_hops,
                    "score": float(path_info["score"]) * decay,
                }
                previous_depth = visited_depth.get(neighbor_id)
                # Record an additional page path for already-seen nodes (<=, not <)
                # but do not re-expand them, so multiple same-depth parents no
                # longer re-push the node onto the frontier and inflate paths_found.
                if previous_depth is not None and previous_depth <= next_hops:
                    if target_type == "page":
                        reachable.setdefault(neighbor_id, []).append(next_path)
                        paths_found += 1
                    continue
                if previous_depth is None:
                    nodes_visited += 1
                visited_depth[neighbor_id] = min(visited_depth.get(neighbor_id, next_hops), next_hops)
                reachable.setdefault(neighbor_id, []).append(next_path)
                paths_found += 1
                if next_hops < expand_hops:
                    next_frontier.append((neighbor_id, next_path))
        frontier = next_frontier

    all_results: dict[str, dict[str, Any]] = {}
    for node_id, paths in reachable.items():
        node = node_index.get(node_id)
        node_type = getattr(getattr(node, "type", None), "value", None)
        if node_id not in hit_page_ids and node_type != "page":
            continue

        if node_id in hit_by_page:
            result = _expanded_base_row(hit_by_page[node_id], hit_page_ids)
        else:
            best_score = max(float(path.get("score", 0.0) or 0.0) for path in paths)
            result = {
                "page_id": node_id,
                "score": best_score,
                "sources": ["graph-expansion"],
                "citations": [],
                "relevance_paths": [],
                "supporting_claims": [],
                "contradicting_claims": [],
                "related_decisions": [],
                "related_traces": [],
                "matched_entities": [],
                "relevant_because": "",
            }

        for path in sorted(paths, key=lambda item: (item.get("hops", 0), -float(item.get("score", 0.0) or 0.0)))[:3]:
            if path.get("path"):
                result["relevance_paths"].append(
                    {
                        "source_type": "page",
                        "source_id": str(path.get("source_id") or ""),
                        "path": path["path"],
                        "explanation": _path_explanation(path["path"]),
                    }
                )
        all_results[node_id] = result

    if ledger is not None:
        _enrich_with_ledger_context(
            ledger,
            all_results,
            include_claims,
            include_traces,
            include_decisions,
            claim_actor=claim_actor,
        )

    # Populate matched_entities from graph expansion paths
    _populate_matched_entities(all_results, reachable)

    for page_id, result in all_results.items():
        result["relevant_because"] = _relevant_because(page_id, result, hit_page_ids)

    ranked = sorted(all_results.values(), key=lambda item: item["score"], reverse=True)[:limit]
    return {
        "query": query,
        "total": len(ranked),
        "results": ranked,
        "expansion_stats": {
            "hops": expand_hops,
            "nodes_visited": nodes_visited,
            "paths_found": paths_found,
        },
    }


def _expanded_base_row(result: dict[str, Any], hit_page_ids: set[str]) -> dict[str, Any]:
    page_id = str(result.get("page_id") or "")
    return {
        "page_id": page_id,
        "score": float(result.get("score", 0.0) or 0.0),
        "sources": list(result.get("sources") or []),
        "citations": list(result.get("citations") or []),
        "relevance_paths": [],
        "supporting_claims": [],
        "contradicting_claims": [],
        "related_decisions": [],
        "related_traces": [],
        "matched_entities": [],
        "relevant_because": "direct text match" if page_id in hit_page_ids else "",
    }


def _context_graph_adjacency(
    context_graph: Any, expand_edge_types: list[str] | None
) -> dict[str, list[tuple[str, Any, str]]]:
    allowed = set(expand_edge_types or [])
    adjacency: dict[str, list[tuple[str, Any, str]]] = {}
    for edge in getattr(context_graph, "edges", []):
        edge_type = getattr(getattr(edge, "type", None), "value", None) or str(getattr(edge, "type", ""))
        if allowed and edge_type not in allowed:
            continue
        adjacency.setdefault(str(edge.source), []).append((str(edge.target), edge, "outgoing"))
        adjacency.setdefault(str(edge.target), []).append((str(edge.source), edge, "incoming"))
    return adjacency


def _query_scoped_context_graph(context_graph: Any, seed_page_ids: set[str], expand_hops: int) -> Any:
    """Merge ego subgraphs around direct hits so expansion avoids full-graph adjacency."""
    try:
        from ..context_graph import ContextGraph, ego_subgraph
    except Exception:
        return context_graph

    nodes_by_id: dict[str, Any] = {}
    edges_by_key: dict[tuple[str, str, str, str, str], Any] = {}
    radius = max(1, expand_hops)

    for page_id in seed_page_ids:
        subgraph = ego_subgraph(context_graph, page_id, radius=radius)
        for node in getattr(subgraph, "nodes", []):
            nodes_by_id[str(node.id)] = node
        for edge in getattr(subgraph, "edges", []):
            edge_type = getattr(getattr(edge, "type", None), "value", None) or str(getattr(edge, "type", ""))
            metadata = getattr(edge, "metadata", {}) or {}
            key = (
                str(edge.source),
                str(edge.target),
                edge_type,
                str(getattr(edge, "label", "") or ""),
                json.dumps(metadata, sort_keys=True, default=str),
            )
            edges_by_key[key] = edge

    stats: dict[str, int] = {}
    for node in nodes_by_id.values():
        node_type = getattr(getattr(node, "type", None), "value", None) or "unknown"
        stats[node_type] = stats.get(node_type, 0) + 1
    stats["edges"] = len(edges_by_key)
    return ContextGraph(nodes=list(nodes_by_id.values()), edges=list(edges_by_key.values()), stats=stats)


def _path_explanation(path: list[dict[str, str]]) -> str:
    return " -> ".join(f"{step.get('edge_type')} -> {step.get('target_type')}" for step in path)


def _relevant_because(page_id: str, result: dict[str, Any], hit_page_ids: set[str]) -> str:
    parts = []
    if page_id in hit_page_ids:
        parts.append("direct text match")
    if result.get("relevance_paths"):
        paths_desc = ", ".join(
            path.get("explanation", "") for path in result["relevance_paths"][:2] if path.get("explanation")
        )
        if paths_desc:
            parts.append(f"reachable via: {paths_desc}")
    if result.get("supporting_claims"):
        parts.append(f"{len(result['supporting_claims'])} supporting claim(s)")
    if result.get("contradicting_claims"):
        parts.append(f"{len(result['contradicting_claims'])} contradicting claim(s)")
    if result.get("related_decisions"):
        parts.append(f"related decision(s): {', '.join(result['related_decisions'][:3])}")
    if result.get("related_traces"):
        parts.append(f"{len(result['related_traces'])} related trace(s)")
    return "; ".join(parts) if parts else "graph expansion"


def _enrich_with_ledger_context(
    ledger: Any,
    all_results: dict[str, dict[str, Any]],
    include_claims: bool,
    include_traces: bool,
    include_decisions: bool,
    *,
    claim_actor: str | None = None,
) -> None:
    """Enrich expanded results with claim, trace, and decision context from the ledger."""
    if include_claims:
        try:
            candidates = ledger.get_candidates(limit=500, actor=claim_actor)
        except Exception:
            candidates = []
        for candidate in candidates:
            candidate_type = str(candidate.get("candidate_type") or "")
            if candidate_type not in {"claim", "invalidation"}:
                continue
            candidate_id = str(candidate.get("candidate_id") or "")
            for page_id in _string_list(candidate.get("source_page_ids")):
                if page_id not in all_results:
                    continue
                if candidate_type == "invalidation":
                    _append_unique(all_results[page_id]["contradicting_claims"], candidate_id)
                else:
                    _append_unique(all_results[page_id]["supporting_claims"], candidate_id)

    if include_traces:
        try:
            traces = ledger.list_traces(limit=200)
        except Exception:
            traces = []
        for trace in traces:
            trace_id = str(getattr(trace, "trace_id", "") or "")
            for ref in getattr(trace, "context_refs", []) or []:
                if getattr(ref, "type", None) == "page" and getattr(ref, "id", None) in all_results:
                    _append_unique(all_results[ref.id]["related_traces"], trace_id)
            provenance = getattr(trace, "provenance", None)
            for page_id in _string_list(getattr(provenance, "page_ids", [])):
                if page_id in all_results:
                    _append_unique(all_results[page_id]["related_traces"], trace_id)

    if include_decisions:
        for page_id, result in all_results.items():
            if page_id.startswith("decisions/"):
                _append_unique(result["related_decisions"], page_id)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return _string_list(decoded)
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and str(item)]
    return []


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _populate_matched_entities(
    all_results: dict[str, dict[str, Any]],
    reachable: dict[str, list[dict[str, Any]]],
) -> None:
    """Populate matched_entities from each page's own graph expansion paths."""
    entity_types = {"entity", "claim", "capture", "trace", "decision", "policy"}
    for page_id, result in all_results.items():
        entities: list[str] = []
        # Collect entity IDs reached on paths originating from this page only.
        # (A prior global scan over `reachable` polluted every result with the
        # same union of all entity nodes in the scoped expansion.)
        for path_info in reachable.get(page_id, []):
            for step in path_info.get("path", []):
                target_type = step.get("target_type", "")
                target_id = step.get("target_id", "")
                if target_type in entity_types and target_id:
                    _append_unique(entities, target_id)
        result["matched_entities"] = entities
