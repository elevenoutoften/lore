from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import TYPE_CHECKING, Any

from .repository import LoreRepository, dict_list, optional_string, string_list
from .schemas import (
    ContextEdgeType,
    ContextExplainQuery,
    ContextExplainResponse,
    ContextGraph,
    ContextGraphEdge,
    ContextGraphNeighbor,
    ContextGraphNeighborQuery,
    ContextGraphNeighborResponse,
    ContextGraphNode,
    ContextGraphPath,
    ContextGraphPathQuery,
    ContextGraphPathResponse,
    ContextGraphPathStep,
    ContextNodeType,
    ProvenanceRef,
)

if TYPE_CHECKING:
    from .ledger import LedgerDB

WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


class ContextGraphCache:
    """Lazy-rebuild cache for ContextGraph, invalidated by page or ledger changes."""

    def __init__(self):
        self._graph: ContextGraph | None = None
        self._page_fingerprint: str = ""
        self._ledger_generation: int = -1

    @staticmethod
    def _page_fingerprint_for(repo: LoreRepository) -> str:
        pages = repo.list_pages_meta()
        raw = json.dumps([(page.id, page.updated_at) for page in pages], separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, repo: LoreRepository, ledger: LedgerDB | None = None) -> ContextGraph:
        page_fp = self._page_fingerprint_for(repo)
        ledger_generation = ledger.generation if ledger is not None else -1
        if (
            self._graph is not None
            and page_fp == self._page_fingerprint
            and ledger_generation == self._ledger_generation
        ):
            return self._graph

        self._graph = build_context_graph(repo, ledger)
        self._page_fingerprint = page_fp
        self._ledger_generation = ledger_generation
        return self._graph

    def invalidate(self) -> None:
        self._graph = None
        self._page_fingerprint = ""
        self._ledger_generation = -1


def build_context_graph(repo: LoreRepository, ledger: LedgerDB | None = None) -> ContextGraph:
    """Build the full context graph deterministically from repo and ledger data."""

    nodes: list[ContextGraphNode] = []
    edges: list[ContextGraphEdge] = []
    node_ids: set[str] = set()
    edge_keys: set[tuple[str, str, str, str, str]] = set()

    def add_node(node: ContextGraphNode) -> None:
        if node.id not in node_ids:
            node_ids.add(node.id)
            nodes.append(node)

    def ensure_node(node_id: str, node_type: ContextNodeType, label: str | None = None, **metadata: Any) -> None:
        add_node(
            ContextGraphNode(id=node_id, type=node_type, label=label or node_id, metadata=_compact_metadata(metadata))
        )

    def add_edge(source: str, target: str, edge_type: ContextEdgeType, label: str = "", **metadata: Any) -> None:
        compact = _compact_metadata(metadata)
        meta_key = json.dumps(compact, sort_keys=True, default=str)
        edge_key = (source, target, edge_type.value, label, meta_key)
        if edge_key in edge_keys:
            return
        edge_keys.add(edge_key)
        edges.append(ContextGraphEdge(source=source, target=target, type=edge_type, label=label, metadata=compact))

    for detail in repo.iter_pages():
        fm = detail.frontmatter
        provenance = _provenance_dict(fm.get("provenance"))
        kind = optional_string(fm.get("kind")) or detail.kind or "page"
        node_type = ContextNodeType.capture if kind == "capture" else ContextNodeType.page
        add_node(
            ContextGraphNode(
                id=detail.id,
                type=node_type,
                label=detail.title or detail.id,
                metadata=_compact_metadata(
                    {
                        "kind": kind,
                        "visibility": optional_string(fm.get("visibility")) or detail.visibility,
                        "status": optional_string(fm.get("status")),
                        "tags": string_list(fm.get("tags")),
                        "observed_at": optional_string(fm.get("observed_at")),
                    }
                ),
            )
        )

        for link in _page_wikilinks(detail.body):
            add_edge(detail.id, link, ContextEdgeType.mentions, "wikilink")

        for source_path in _dedupe(
            string_list(fm.get("source_paths"))
            + string_list(provenance.get("source_paths"))
            + [source for source in string_list(fm.get("sources")) if not _looks_like_url(source)]
        ):
            source_id = _source_node_id(source_path)
            ensure_node(source_id, ContextNodeType.source, source_path, path=source_path)
            add_edge(source_id, detail.id, ContextEdgeType.source_of, source_path)

        for source_url in _dedupe(
            string_list(fm.get("source_urls"))
            + string_list(provenance.get("source_urls"))
            + [source for source in string_list(fm.get("sources")) if _looks_like_url(source)]
        ):
            source_id = _source_node_id(source_url)
            ensure_node(source_id, ContextNodeType.source, source_url, url=source_url)
            add_edge(source_id, detail.id, ContextEdgeType.source_of, source_url)

        for task_id in _dedupe(
            string_list(fm.get("task_id"))
            + string_list(fm.get("decision_id"))
            + string_list(provenance.get("task_ids"))
            + string_list(provenance.get("source_task"))
        ):
            task_node_id = f"task:{task_id}"
            ensure_node(task_node_id, ContextNodeType.task, task_id)
            add_edge(detail.id, task_node_id, ContextEdgeType.task_related, task_id)

        for trace_id in _dedupe(string_list(fm.get("trace_id")) + string_list(provenance.get("trace_ids"))):
            trace_node_id = f"trace:{trace_id}"
            ensure_node(trace_node_id, ContextNodeType.trace, trace_id, referenced=True)
            add_edge(detail.id, trace_node_id, ContextEdgeType.provenance, trace_id)

        for policy_id in _dedupe(string_list(fm.get("policies_applied")) + string_list(provenance.get("policy_ids"))):
            policy_node_id = f"policy:{policy_id}"
            ensure_node(policy_node_id, ContextNodeType.policy, policy_id, referenced=True)
            add_edge(detail.id, policy_node_id, ContextEdgeType.used_policy, policy_id)

        for related_page_id in _dedupe(string_list(fm.get("related_pages")) + string_list(provenance.get("page_ids"))):
            add_edge(detail.id, related_page_id, ContextEdgeType.provenance, related_page_id)

        for capture_id in _dedupe(string_list(provenance.get("capture_ids"))):
            add_edge(detail.id, capture_id, ContextEdgeType.provenance, capture_id)

        for candidate_id in _dedupe(string_list(provenance.get("candidate_ids"))):
            candidate_node_id = f"candidate:{candidate_id}"
            ensure_node(candidate_node_id, ContextNodeType.entity, candidate_id, referenced=True)
            add_edge(detail.id, candidate_node_id, ContextEdgeType.provenance, candidate_id)

        actor = optional_string(fm.get("actor")) or optional_string(provenance.get("actor"))
        if actor:
            actor_id = f"actor:{actor}"
            ensure_node(actor_id, ContextNodeType.actor, actor)
            add_edge(actor_id, detail.id, ContextEdgeType.authored, actor)

        for tool_call in dict_list(fm.get("tool_calls")) + dict_list(provenance.get("tool_calls")):
            tool_id, label = _tool_node(tool_call)
            ensure_node(tool_id, ContextNodeType.tool, label, **tool_call)
            add_edge(detail.id, tool_id, ContextEdgeType.used_tool, label)

    if ledger is not None:
        _add_ledger_nodes(ledger, add_node, ensure_node, add_edge)

    stats: dict[str, int] = {}
    for node in nodes:
        stats[node.type.value] = stats.get(node.type.value, 0) + 1
    stats["edges"] = len(edges)

    return ContextGraph(nodes=nodes, edges=edges, stats=stats)


def scope_context_graph(graph: ContextGraph, actor: str | None) -> ContextGraph:
    """Remove graph nodes that expose another authenticated actor's memory."""
    if not actor:
        return graph

    actor_node_id = f"actor:{actor}"
    kept_nodes = []
    for node in graph.nodes:
        node_actor = optional_string(node.metadata.get("actor"))
        if node.type == ContextNodeType.actor and node.id != actor_node_id:
            continue
        if node_actor and node_actor != actor:
            continue
        kept_nodes.append(node)

    kept_ids = {node.id for node in kept_nodes}
    kept_edges = [edge for edge in graph.edges if edge.source in kept_ids and edge.target in kept_ids]
    stats: dict[str, int] = {}
    for node in kept_nodes:
        stats[node.type.value] = stats.get(node.type.value, 0) + 1
    stats["edges"] = len(kept_edges)
    return ContextGraph(nodes=kept_nodes, edges=kept_edges, stats=stats)


def query_neighbors(graph: ContextGraph, query: ContextGraphNeighborQuery) -> ContextGraphNeighborResponse:
    """Find neighbors of a node in the context graph."""

    node_index = {node.id: node for node in graph.nodes}
    neighbors: list[ContextGraphNeighbor] = []

    for edge in graph.edges:
        if query.edge_types and edge.type.value not in query.edge_types:
            continue

        neighbor_id: str | None = None
        if query.direction in ("outgoing", "both") and edge.source == query.node_id:
            neighbor_id = edge.target
        elif query.direction in ("incoming", "both") and edge.target == query.node_id:
            neighbor_id = edge.source

        if neighbor_id is None:
            continue

        neighbor_node = node_index.get(neighbor_id)
        if neighbor_node is None:
            continue
        if query.node_types and neighbor_node.type.value not in query.node_types:
            continue
        neighbors.append(ContextGraphNeighbor(node=neighbor_node, edge=edge))

    total = len(neighbors)
    return ContextGraphNeighborResponse(node_id=query.node_id, neighbors=neighbors[: query.limit], total=total)


def neighbors_of(graph: ContextGraph, node_id: str, depth: int = 1) -> ContextGraphNeighborResponse:
    """Return neighbors of a cached graph node within the given depth."""

    depth = max(1, depth)
    node_index = {node.id: node for node in graph.nodes}
    visited: set[str] = {node_id}
    frontier: set[str] = {node_id}
    neighbors: list[ContextGraphNeighbor] = []

    for _ in range(depth):
        next_frontier: set[str] = set()
        for edge in graph.edges:
            neighbor_id: str | None = None
            if edge.source in frontier and edge.target not in visited:
                neighbor_id = edge.target
            elif edge.target in frontier and edge.source not in visited:
                neighbor_id = edge.source
            if neighbor_id is None:
                continue
            neighbor_node = node_index.get(neighbor_id)
            if neighbor_node is None:
                continue
            neighbors.append(ContextGraphNeighbor(node=neighbor_node, edge=edge))
            visited.add(neighbor_id)
            next_frontier.add(neighbor_id)
        frontier = next_frontier
        if not frontier:
            break

    return ContextGraphNeighborResponse(node_id=node_id, neighbors=neighbors, total=len(neighbors))


def query_paths(graph: ContextGraph, query: ContextGraphPathQuery) -> ContextGraphPathResponse:
    """Find bounded paths between two nodes using BFS over outgoing edges."""

    node_index = {node.id: node for node in graph.nodes}
    adjacency: dict[str, list[tuple[ContextGraphEdge, ContextGraphNode]]] = {}
    for edge in graph.edges:
        if query.edge_types and edge.type.value not in query.edge_types:
            continue
        target_node = node_index.get(edge.target)
        if target_node is not None:
            adjacency.setdefault(edge.source, []).append((edge, target_node))

    paths: list[ContextGraphPath] = []
    visited_paths: set[tuple[str, ...]] = set()
    queue: deque[tuple[str, list[ContextGraphPathStep], set[str]]] = deque([(query.source_id, [], {query.source_id})])

    while queue and len(paths) < query.limit:
        current, steps, seen = queue.popleft()

        if current == query.target_id and steps:
            path_key = tuple(f"{step.edge.source}->{step.edge.target}:{step.edge.type.value}" for step in steps)
            if path_key not in visited_paths:
                visited_paths.add(path_key)
                paths.append(
                    ContextGraphPath(
                        source_id=query.source_id,
                        target_id=query.target_id,
                        steps=steps,
                        length=len(steps),
                    )
                )
            continue

        if len(steps) >= query.max_depth:
            continue

        for edge, node in adjacency.get(current, []):
            if node.id in seen:
                continue
            queue.append((node.id, [*steps, ContextGraphPathStep(edge=edge, node=node)], seen | {node.id}))

    return ContextGraphPathResponse(source_id=query.source_id, target_id=query.target_id, paths=paths)


def ego_subgraph(graph: ContextGraph, center_id: str, radius: int = 1) -> ContextGraph:
    """Return the cached graph subset centered on a node within the given radius."""

    neighbor_response = neighbors_of(graph, center_id, depth=radius)
    keep_ids = {center_id, *(neighbor.node.id for neighbor in neighbor_response.neighbors)}
    nodes = [node for node in graph.nodes if node.id in keep_ids]
    edges = [edge for edge in graph.edges if edge.source in keep_ids and edge.target in keep_ids]
    stats: dict[str, int] = {}
    for node in nodes:
        stats[node.type.value] = stats.get(node.type.value, 0) + 1
    stats["edges"] = len(edges)
    return ContextGraph(nodes=nodes, edges=edges, stats=stats)


def path_between(graph: ContextGraph, source_id: str, target_id: str) -> ContextGraphPathResponse:
    """Find shortest paths between two nodes in a cached graph."""

    return query_paths(graph, ContextGraphPathQuery(source_id=source_id, target_id=target_id, limit=1))


def explain_context(graph: ContextGraph, query: ContextExplainQuery) -> ContextExplainResponse:
    """Explain the context around a node by expanding its neighborhood."""

    node_index = {node.id: node for node in graph.nodes}
    node = node_index.get(query.node_id)
    if node is None:
        return ContextExplainResponse(
            node=ContextGraphNode(id=query.node_id, type=ContextNodeType.entity, label=query.node_id),
            neighborhood=[],
            explanation=f"Node {query.node_id} not found in context graph.",
        )

    visited: set[str] = {query.node_id}
    frontier = [query.node_id]
    all_neighbors: list[ContextGraphNeighbor] = []

    for _ in range(query.depth):
        next_frontier: list[str] = []
        for frontier_id in frontier:
            for edge in graph.edges:
                if query.edge_types and edge.type.value not in query.edge_types:
                    continue

                neighbor_id: str | None = None
                if edge.source == frontier_id and edge.target not in visited:
                    neighbor_id = edge.target
                elif edge.target == frontier_id and edge.source not in visited:
                    neighbor_id = edge.source

                if neighbor_id is None:
                    continue

                neighbor_node = node_index.get(neighbor_id)
                if neighbor_node is None:
                    continue
                all_neighbors.append(ContextGraphNeighbor(node=neighbor_node, edge=edge))
                visited.add(neighbor_id)
                next_frontier.append(neighbor_id)
        frontier = next_frontier

    edge_summary: dict[str, int] = {}
    for neighbor in all_neighbors:
        edge_summary[neighbor.edge.type.value] = edge_summary.get(neighbor.edge.type.value, 0) + 1

    lines = [f"Context for {node.type.value} '{node.label}' ({node.id}):"]
    for edge_type, count in sorted(edge_summary.items()):
        lines.append(f"  {edge_type}: {count} connection(s)")
    lines.append(f"Total neighbors: {len(all_neighbors)}")

    return ContextExplainResponse(
        node=node,
        neighborhood=all_neighbors,
        explanation="\n".join(lines),
    )


def _add_ledger_nodes(
    ledger: LedgerDB,
    add_node: Any,
    ensure_node: Any,
    add_edge: Any,
) -> None:
    # Only live claims (candidate/active) belong in the graph; rejected/superseded/
    # archived claims must not surface in recall expansion. max_rows lifts the
    # default 500 clamp so a large ledger's older live claims are not truncated.
    for candidate in _safe_call(
        lambda: ledger.get_candidates(statuses=("candidate", "active"), limit=10000, max_rows=10000)
    ):
        candidate_id = optional_string(candidate.get("candidate_id"))
        if not candidate_id:
            continue

        candidate_type = optional_string(candidate.get("candidate_type")) or "entity"
        node_type = {
            "claim": ContextNodeType.claim,
            "invalidation": ContextNodeType.invalidation,
            "entity": ContextNodeType.entity,
        }.get(candidate_type, ContextNodeType.entity)
        candidate_node_id = f"candidate:{candidate_id}"
        add_node(
            ContextGraphNode(
                id=candidate_node_id,
                type=node_type,
                label=_candidate_label(candidate),
                metadata={
                    "batch_id": candidate.get("batch_id"),
                    "candidate_type": candidate_type,
                    "status": candidate.get("status"),
                    "confidence": candidate.get("confidence"),
                    "strength": candidate.get("strength"),
                    "lane": candidate.get("lane"),
                    "actor": candidate.get("actor"),
                    "observed_at": candidate.get("observed_at"),
                    "valid_from": candidate.get("valid_from"),
                    "valid_until": candidate.get("valid_until"),
                },
            )
        )

        # Materialize placeholder endpoints so provenance edges to a missing
        # page/capture are traversable rather than silently dropped at traversal
        # time (which would make stats['edges'] overcount reachable edges).
        # ensure_node is idempotent and runs after all real page nodes are added,
        # so real pages/captures keep their richer metadata.
        for capture_id in string_list(candidate.get("source_capture_ids")):
            ensure_node(capture_id, ContextNodeType.capture, capture_id, placeholder=True)
            add_edge(capture_id, candidate_node_id, ContextEdgeType.generated, capture_id)

        for page_id in string_list(candidate.get("source_page_ids")):
            ensure_node(page_id, ContextNodeType.page, page_id, placeholder=True)
            if node_type == ContextNodeType.invalidation:
                add_edge(candidate_node_id, page_id, ContextEdgeType.contradicts, page_id)
            else:
                add_edge(candidate_node_id, page_id, ContextEdgeType.supports, page_id)

        supersedes = optional_string(candidate.get("supersedes"))
        if supersedes:
            add_edge(candidate_node_id, f"candidate:{supersedes}", ContextEdgeType.supersedes, supersedes)
        superseded_by = optional_string(candidate.get("superseded_by"))
        if superseded_by:
            add_edge(f"candidate:{superseded_by}", candidate_node_id, ContextEdgeType.supersedes, candidate_id)

        actor = optional_string(candidate.get("actor"))
        if actor:
            actor_id = f"actor:{actor}"
            ensure_node(actor_id, ContextNodeType.actor, actor)
            add_edge(actor_id, candidate_node_id, ContextEdgeType.authored, actor)

    for plan in _safe_call(lambda: ledger.list_patch_plans(limit=10000)):
        plan_id = optional_string(plan.get("plan_id"))
        if not plan_id:
            continue

        plan_node_id = f"plan:{plan_id}"
        add_node(
            ContextGraphNode(
                id=plan_node_id,
                type=ContextNodeType.plan,
                label=plan_id,
                metadata={
                    "batch_id": plan.get("batch_id"),
                    "operation": plan.get("operation"),
                    "risk_level": plan.get("risk_level"),
                    "auto_appliable": bool(plan.get("auto_appliable")),
                    "status": plan.get("status"),
                },
            )
        )

        trace_id = optional_string(plan.get("trace_id"))
        if trace_id:
            trace_node_id = f"trace:{trace_id}"
            ensure_node(trace_node_id, ContextNodeType.trace, trace_id, referenced=True)
            add_edge(trace_node_id, plan_node_id, ContextEdgeType.generated, trace_id)

        target_page_id = optional_string(plan.get("target_page_id"))
        if target_page_id:
            add_edge(plan_node_id, target_page_id, ContextEdgeType.applied, target_page_id)

        for candidate_id in string_list(plan.get("candidate_ids")):
            add_edge(plan_node_id, f"candidate:{candidate_id}", ContextEdgeType.applied, candidate_id)

        for policy_id in _plan_policy_ids(plan.get("policies_applied")):
            policy_node_id = f"policy:{policy_id}"
            ensure_node(policy_node_id, ContextNodeType.policy, policy_id, referenced=True)
            add_edge(plan_node_id, policy_node_id, ContextEdgeType.used_policy, policy_id)

    for trace in _safe_call(lambda: ledger.list_traces(limit=10000)):
        trace_node_id = f"trace:{trace.trace_id}"
        add_node(
            ContextGraphNode(
                id=trace_node_id,
                type=ContextNodeType.trace,
                label=trace.trace_id,
                metadata={
                    "actor": trace.actor,
                    "status": trace.status,
                    "reason_summary": trace.reason_summary[:100] if trace.reason_summary else "",
                },
            )
        )

        if trace.parent_trace_id:
            add_edge(f"trace:{trace.parent_trace_id}", trace_node_id, ContextEdgeType.parent, trace.parent_trace_id)

        for ref in trace.context_refs:
            target = _context_ref_node_id(ref.type, ref.id)
            add_edge(trace_node_id, target, ContextEdgeType.provenance, ref.id)

        provenance = trace.provenance.model_dump(mode="json") if isinstance(trace.provenance, ProvenanceRef) else {}
        for page_id in string_list(provenance.get("page_ids")):
            ensure_node(page_id, ContextNodeType.page, page_id, placeholder=True)
            add_edge(trace_node_id, page_id, ContextEdgeType.provenance, page_id)
        for capture_id in string_list(provenance.get("capture_ids")):
            ensure_node(capture_id, ContextNodeType.capture, capture_id, placeholder=True)
            add_edge(trace_node_id, capture_id, ContextEdgeType.provenance, capture_id)
        for candidate_id in string_list(provenance.get("candidate_ids")):
            add_edge(trace_node_id, f"candidate:{candidate_id}", ContextEdgeType.provenance, candidate_id)
        for task_id in string_list(provenance.get("task_ids")):
            task_node_id = f"task:{task_id}"
            ensure_node(task_node_id, ContextNodeType.task, task_id)
            add_edge(trace_node_id, task_node_id, ContextEdgeType.task_related, task_id)
        for source_path in string_list(provenance.get("source_paths")):
            source_id = _source_node_id(source_path)
            ensure_node(source_id, ContextNodeType.source, source_path, path=source_path)
            add_edge(source_id, trace_node_id, ContextEdgeType.source_of, source_path)
        for source_url in string_list(provenance.get("source_urls")):
            source_id = _source_node_id(source_url)
            ensure_node(source_id, ContextNodeType.source, source_url, url=source_url)
            add_edge(source_id, trace_node_id, ContextEdgeType.source_of, source_url)

        if trace.actor:
            actor_id = f"actor:{trace.actor}"
            ensure_node(actor_id, ContextNodeType.actor, trace.actor)
            add_edge(actor_id, trace_node_id, ContextEdgeType.authored, trace.actor)

        for policy_id in trace.policy_refs:
            policy_node_id = f"policy:{policy_id}"
            ensure_node(policy_node_id, ContextNodeType.policy, policy_id, referenced=True)
            add_edge(trace_node_id, policy_node_id, ContextEdgeType.used_policy, policy_id)

        for tool_ref in trace.tool_refs:
            tool_payload = tool_ref.model_dump(mode="json")
            tool_id, label = _tool_node(tool_payload)
            ensure_node(tool_id, ContextNodeType.tool, label, **tool_payload)
            add_edge(trace_node_id, tool_id, ContextEdgeType.used_tool, label)

    for policy in _safe_call(lambda: ledger.list_policies(enabled_only=False)):
        add_node(
            ContextGraphNode(
                id=f"policy:{policy.policy_id}",
                type=ContextNodeType.policy,
                label=policy.name or policy.policy_id,
                metadata={
                    "gate": policy.gate,
                    "version": policy.version,
                    "enabled": policy.enabled,
                    "condition_kind": policy.condition_kind,
                    "condition_operation": policy.condition_operation,
                },
            )
        )


def _page_wikilinks(body: str) -> list[str]:
    targets: list[str] = []
    for left, right in WIKILINK_PATTERN.findall(body):
        left = left.strip()
        right = right.strip()
        if right and _looks_like_page_id(right):
            targets.append(right)
        elif _looks_like_page_id(left):
            targets.append(left)
        elif right:
            targets.append(right)
        elif left:
            targets.append(left)
    return targets


def _safe_call(call: Any) -> list[Any]:
    try:
        result = call()
    except Exception:
        return []
    return result if isinstance(result, list) else []


def _provenance_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, ProvenanceRef):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _plan_policy_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return string_list(value)
    if not isinstance(value, list):
        return []
    policy_ids: list[str] = []
    for item in value:
        policy_id = optional_string(item.get("policy_id")) if isinstance(item, dict) else optional_string(item)
        if policy_id:
            policy_ids.append(policy_id)
    return _dedupe(policy_ids)


def _context_ref_node_id(ref_type: str, ref_id: str) -> str:
    if ref_type in {"page", "capture"}:
        return ref_id
    if ref_type == "candidate":
        return f"candidate:{ref_id}"
    if ref_type == "tool":
        return f"tool:{ref_id}"
    return f"{ref_type}:{ref_id}"


def _source_node_id(source: str) -> str:
    return f"source:{source}"


def _tool_node(tool_call: dict[str, Any]) -> tuple[str, str]:
    tool = optional_string(tool_call.get("tool")) or optional_string(tool_call.get("name")) or "tool"
    action = optional_string(tool_call.get("action"))
    label = f"{tool}:{action}" if action else tool
    digest = hashlib.sha1(json.dumps(tool_call, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    return f"tool:{label}:{digest}", label


def _candidate_label(candidate: dict[str, Any]) -> str:
    content = candidate.get("content_json")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}
    if isinstance(content, dict):
        if {"subject", "predicate", "object"} <= set(content):
            return f"{content['subject']} {content['predicate']} {content['object']}"
        if "name" in content:
            return str(content["name"])
        if "new_fact" in content:
            return str(content["new_fact"])
    return str(candidate.get("candidate_id") or "")


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _looks_like_page_id(value: str) -> bool:
    return "/" in value or value == value.casefold()
