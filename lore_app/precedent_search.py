from __future__ import annotations

from typing import Any

from .context_graph import build_context_graph, query_neighbors, query_paths
from .ledger import LedgerDB
from .repository import LoreRepository, optional_string, string_list
from .schemas import (
    ContextGraphNeighborQuery,
    ContextGraphPathQuery,
    PrecedentResult,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
)


def search_precedents(
    repo: LoreRepository,
    ledger: LedgerDB | None,
    request: PrecedentSearchRequest,
) -> PrecedentSearchResponse:
    """Search for prior traces, policies, decisions, captures, and related pages."""

    results: list[PrecedentResult] = []
    seen_ids: set[str] = set()

    if ledger is not None:
        _search_traces(ledger, request, results, seen_ids)
        _search_policies(ledger, request, results, seen_ids)

    _search_pages(repo, request, results, seen_ids)

    graph_target: str | None = None
    if request.entity or request.task_ref:
        graph_target = _search_via_context_graph(repo, ledger, request, results, seen_ids)
    if graph_target:
        _enrich_graph_paths(repo, ledger, graph_target, results)

    results.sort(key=lambda result: (-result.relevance, result.type, result.id))
    limited = results[: request.limit]
    return PrecedentSearchResponse(matches=limited, total=len(limited))


def _search_traces(
    ledger: LedgerDB,
    request: PrecedentSearchRequest,
    results: list[PrecedentResult],
    seen_ids: set[str],
) -> None:
    try:
        traces = ledger.list_traces(limit=500)
    except Exception:
        return

    keyword = _needle(request.keyword)
    situation = _needle(request.situation_type)
    entity = _needle(request.entity)

    for trace in traces:
        if request.actor and trace.actor != request.actor:
            continue
        if request.policy and request.policy not in trace.policy_refs:
            continue
        if request.task_ref and not _trace_has_task_ref(trace, request.task_ref):
            continue
        if keyword and keyword not in _trace_haystack(trace):
            continue
        if request.lane and request.lane not in _trace_lanes(trace):
            continue

        score = 0.2
        if request.actor and trace.actor == request.actor:
            score += 0.2
        if keyword:
            score += 0.35
        if request.policy and request.policy in trace.policy_refs:
            score += 0.3
        if situation and situation in _needle(trace.reason_summary):
            score += 0.15
        if entity and entity in _trace_haystack(trace):
            score += 0.2
        if request.task_ref:
            score += 0.15

        result_id = f"trace:{trace.trace_id}"
        if result_id in seen_ids:
            continue
        seen_ids.add(result_id)
        results.append(
            PrecedentResult(
                type="trace",
                id=result_id,
                title=(trace.reason_summary or trace.trace_id)[:100],
                summary=(trace.reason_summary or "")[:200],
                outcome=trace.outcome or trace.status,
                actor=trace.actor,
                related_policies=list(trace.policy_refs),
                relevance=score,
            )
        )


def _search_policies(
    ledger: LedgerDB,
    request: PrecedentSearchRequest,
    results: list[PrecedentResult],
    seen_ids: set[str],
) -> None:
    if not request.policy and not request.keyword and not request.entity:
        return

    try:
        policies = ledger.list_policies(enabled_only=False)
    except Exception:
        return

    keyword = _needle(request.keyword)
    entity = _needle(request.entity)
    for policy in policies:
        haystack = _needle(" ".join(part for part in [policy.policy_id, policy.name, policy.description] if part))
        if request.policy and policy.policy_id != request.policy:
            continue
        if keyword and keyword not in haystack:
            continue
        if entity and entity not in haystack:
            continue

        result_id = f"policy:{policy.policy_id}"
        if result_id in seen_ids:
            continue
        seen_ids.add(result_id)
        results.append(
            PrecedentResult(
                type="policy",
                id=result_id,
                title=policy.name or policy.policy_id,
                summary=policy.description or "",
                outcome="enabled" if policy.enabled else "disabled",
                related_policies=[policy.policy_id],
                relevance=0.6 if request.policy == policy.policy_id else 0.25,
            )
        )


def _search_pages(
    repo: LoreRepository,
    request: PrecedentSearchRequest,
    results: list[PrecedentResult],
    seen_ids: set[str],
) -> None:
    keyword = _needle(request.keyword)
    entity = _needle(request.entity)
    situation = _needle(request.situation_type)

    for page in repo.list_pages():
        detail = repo.read_page(page.id)
        if detail is None:
            continue
        fm = detail.frontmatter
        kind = optional_string(fm.get("kind")) or page.kind or "page"
        title = page.title or page.id
        body_text = detail.body or detail.content
        haystack = _needle(" ".join(filter(None, [page.id, title, page.summary or "", body_text])))
        task_values = {
            value
            for value in [
                optional_string(fm.get("task_id")),
                optional_string(fm.get("source_task")),
                optional_string(page.source_task),
                optional_string(fm.get("decision_id")),
            ]
            if value
        }

        if request.lane and optional_string(fm.get("lane")) != request.lane:
            continue
        if request.actor and optional_string(fm.get("actor")) != request.actor:
            continue
        if request.task_ref and request.task_ref not in task_values:
            provenance = fm.get("provenance") if isinstance(fm.get("provenance"), dict) else {}
            if request.task_ref not in string_list(provenance.get("task_ids")):
                continue
        if request.policy and request.policy not in string_list(fm.get("policies_applied")):
            provenance = fm.get("provenance") if isinstance(fm.get("provenance"), dict) else {}
            if request.policy not in string_list(provenance.get("policy_ids")):
                continue
        if keyword and keyword not in haystack:
            continue
        if entity and entity not in haystack:
            continue
        if situation and situation not in _needle(" ".join(filter(None, [kind, page.summary or "", body_text]))):
            continue

        is_decision = kind == "decision"
        is_capture = kind == "capture"
        result_type = "decision" if is_decision else "capture" if is_capture else "page"
        score = 0.1
        if is_decision:
            score += 0.35
        if is_capture:
            score += 0.1
        if keyword:
            score += 0.25
        if entity:
            score += 0.2
        if request.actor and optional_string(fm.get("actor")) == request.actor:
            score += 0.2
        if request.policy and request.policy in string_list(fm.get("policies_applied")):
            score += 0.2
        if request.task_ref and request.task_ref in task_values:
            score += 0.2
        if request.lane and optional_string(fm.get("lane")) == request.lane:
            score += 0.15

        if page.id in seen_ids:
            continue
        seen_ids.add(page.id)
        results.append(
            PrecedentResult(
                type=result_type,
                id=page.id,
                title=title,
                summary=(optional_string(fm.get("summary")) or page.summary or "")[:200],
                outcome=optional_string(fm.get("status")) or page.status or "",
                actor=optional_string(fm.get("actor")) or "",
                related_policies=string_list(fm.get("policies_applied")),
                relevance=score,
            )
        )


def _search_via_context_graph(
    repo: LoreRepository,
    ledger: LedgerDB | None,
    request: PrecedentSearchRequest,
    results: list[PrecedentResult],
    seen_ids: set[str],
) -> str | None:
    try:
        graph = build_context_graph(repo, ledger)
    except Exception:
        return None

    target_node = _graph_target_node(graph, request)
    if target_node is None:
        return None

    try:
        neighbors = query_neighbors(graph, ContextGraphNeighborQuery(node_id=target_node, direction="both", limit=50))
    except Exception:
        return target_node

    for neighbor in neighbors.neighbors:
        node_id = neighbor.node.id
        if node_id in seen_ids:
            continue
        node_type = neighbor.node.type.value
        if node_type not in {"trace", "decision", "page", "policy", "capture"}:
            continue
        seen_ids.add(node_id)
        results.append(
            PrecedentResult(
                type=node_type,
                id=node_id,
                title=neighbor.node.label,
                summary="",
                outcome="",
                graph_paths=[{"from": target_node, "edge": neighbor.edge.type.value, "to": node_id}],
                relevance=0.15,
            )
        )
    return target_node


def _enrich_graph_paths(repo: LoreRepository, ledger: LedgerDB | None, target_node: str, results: list[PrecedentResult]) -> None:
    try:
        graph = build_context_graph(repo, ledger)
    except Exception:
        return

    graph_ids = {node.id for node in graph.nodes}
    for result in results:
        if result.graph_paths:
            continue
        candidate_nodes = _result_graph_node_ids(result)
        for candidate in candidate_nodes:
            if candidate not in graph_ids:
                continue
            try:
                path_response = query_paths(
                    graph,
                    ContextGraphPathQuery(source_id=target_node, target_id=candidate, max_depth=3, limit=1),
                )
            except Exception:
                continue
            if not path_response.paths:
                continue
            path = path_response.paths[0]
            result.graph_paths = [
                {"from": step.edge.source, "edge": step.edge.type.value, "to": step.edge.target}
                for step in path.steps
            ]
            break


def _graph_target_node(graph: Any, request: PrecedentSearchRequest) -> str | None:
    if request.task_ref:
        task_node = f"task:{request.task_ref}"
        if any(node.id == task_node for node in graph.nodes):
            return task_node
    if request.entity:
        entity = request.entity.casefold()
        for node in graph.nodes:
            if entity in node.label.casefold():
                return node.id
    return None


def _result_graph_node_ids(result: PrecedentResult) -> list[str]:
    if result.type == "trace" and not result.id.startswith("trace:"):
        return [f"trace:{result.id}"]
    if result.type == "policy" and not result.id.startswith("policy:"):
        return [f"policy:{result.id}"]
    return [result.id]


def _trace_has_task_ref(trace: Any, task_ref: str) -> bool:
    if task_ref in (trace.related_ids or {}).keys() or task_ref in (trace.related_ids or {}).values():
        return True
    provenance = trace.provenance
    return bool(provenance and task_ref in getattr(provenance, "task_ids", []))


def _trace_lanes(trace: Any) -> set[str]:
    provenance = trace.provenance
    lanes: set[str] = set()
    if provenance is not None:
        lanes.update(string_list(getattr(provenance, "lanes", None)))
    return lanes


def _trace_haystack(trace: Any) -> str:
    parts = [
        trace.trace_id,
        trace.actor,
        trace.reason_summary,
        trace.outcome,
        " ".join(trace.policy_refs),
        " ".join(str(value) for value in (trace.related_ids or {}).values()),
    ]
    provenance = trace.provenance
    if provenance is not None:
        parts.extend(
            [
                " ".join(getattr(provenance, "task_ids", []) or []),
                " ".join(getattr(provenance, "page_ids", []) or []),
                " ".join(getattr(provenance, "capture_ids", []) or []),
            ]
        )
    return _needle(" ".join(part for part in parts if part))


def _needle(value: str | None) -> str:
    return " ".join((value or "").casefold().split())
