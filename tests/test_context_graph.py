from __future__ import annotations

from lore_app.context_graph import build_context_graph, explain_context, query_neighbors, query_paths
from lore_app.schemas import ContextExplainQuery, ContextGraphNeighborQuery, ContextGraphPathQuery
from lore_app.schemas import (
    ContextRef,
    ExtractedClaim,
    ExtractedEntity,
    ExtractionResult,
    PatchOperation,
    PatchPlan,
    PolicyDecision,
    RiskLevel,
    ToolRef,
    TraceEntry,
)


def _node(graph, node_id: str):
    return next((node for node in graph.nodes if node.id == node_id), None)


def _edge(graph, source: str, target: str, edge_type: str):
    return next((edge for edge in graph.edges if edge.source == source and edge.target == target and edge.type == edge_type), None)


def _write_context_pages(repo) -> None:
    repo.upsert_page(
        "services/context-graph-service",
        """---
title: Context Graph Service
kind: service
visibility: internal
status: active
actor: nyx
task_id: flow_000586
sources:
  - README.md
source_urls:
  - https://example.com/context-graph
---

# Context Graph Service

See [[decisions/context-graph-decision]].
""",
    )
    repo.upsert_page(
        "decisions/context-graph-decision",
        """---
title: Context Graph Decision
kind: decision
visibility: internal
status: accepted
---

# Context Graph Decision
""",
    )


def _create_capture(client) -> str:
    response = client.post(
        "/api/capture",
        json={
            "title": "Context graph capture",
            "observation": "Context graph capture observation.",
            "capture_date": "2026-05-01",
            "actor": "nyx",
            "task_id": "flow_000586",
            "source_paths": ["captures/context.md"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def _seed_ledger(client, capture_id: str) -> dict[str, str]:
    ledger = client.app.state.ledger_db
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-context-graph",
            entities=[ExtractedEntity(name="Lore", entity_type="service", summary="Lore service")],
            claims=[
                ExtractedClaim(
                    subject="Lore",
                    predicate="has",
                    object="context graph",
                    confidence="high",
                    actor="nyx",
                    lane="project",
                    source_page_ids=["services/context-graph-service"],
                )
            ],
            source_capture_ids=[capture_id],
            processed_at="2026-05-01T00:00:00+00:00",
        )
    )
    claims = ledger.get_candidates(candidate_type="claim", limit=200)
    entities = ledger.get_candidates(candidate_type="entity", limit=200)
    claim_id = next(candidate["candidate_id"] for candidate in claims if candidate["batch_id"] == "batch-context-graph")
    entity_id = next(candidate["candidate_id"] for candidate in entities if candidate["batch_id"] == "batch-context-graph")

    ledger.store_trace(
        TraceEntry(
            trace_id="trace-context-graph",
            actor="nyx",
            reason_summary="Trace for context graph test.",
            status="completed",
            context_refs=[ContextRef(type="page", id="services/context-graph-service"), ContextRef(type="candidate", id=claim_id)],
            tool_refs=[ToolRef(tool="pytest", action="run", result_summary="passed")],
            policy_refs=["auto-apply:v1"],
            related_ids={"task_id": "flow_000586", "candidate_id": claim_id},
        )
    )
    ledger.store_patch_plan(
        PatchPlan(
            plan_id="plan-context-graph",
            trace_id="trace-context-graph",
            candidate_ids=[claim_id],
            target_page_id="services/context-graph-service",
            operation=PatchOperation.insert_new_fact,
            content_diff="+ Context graph fact.",
            risk_level=RiskLevel.low,
            auto_appliable=True,
            policies_applied=[PolicyDecision(policy_id="auto-apply:v1", gate="auto-apply", passed=True, reason="allowed")],
            status="pending",
            created_at="2026-05-01T00:00:00+00:00",
        ),
        batch_id="batch-context-graph",
    )
    return {"claim_id": claim_id, "entity_id": entity_id}


def _context_graph_fixture(client):
    repo = client.app.state.repository
    _write_context_pages(repo)
    capture_id = _create_capture(client)
    candidate_ids = _seed_ledger(client, capture_id)
    return repo, client.app.state.ledger_db, capture_id, candidate_ids


def _rpc(client, name: str, arguments: dict):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        headers={"Mcp-Method": "tools/call"},
    )


def test_context_graph_includes_page_nodes(client):
    repo = client.app.state.repository
    _write_context_pages(repo)

    graph = build_context_graph(repo, client.app.state.ledger_db)

    assert _node(graph, "services/context-graph-service").type == "page"
    assert _node(graph, "decisions/context-graph-decision").type == "page"


def test_context_graph_includes_capture_nodes(client):
    repo = client.app.state.repository
    capture_id = _create_capture(client)

    graph = build_context_graph(repo, client.app.state.ledger_db)

    assert _node(graph, capture_id).type == "capture"


def test_context_graph_includes_entity_and_claim_nodes(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)

    graph = build_context_graph(repo, ledger)

    assert _node(graph, f"candidate:{candidate_ids['claim_id']}").type == "claim"
    assert _node(graph, f"candidate:{candidate_ids['entity_id']}").type == "entity"


def test_context_graph_includes_plan_and_trace_nodes(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)

    graph = build_context_graph(repo, ledger)

    assert _node(graph, "plan:plan-context-graph").type == "plan"
    assert _node(graph, "trace:trace-context-graph").type == "trace"


def test_context_graph_edges_cover_key_relationships(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    claim_node_id = f"candidate:{candidate_ids['claim_id']}"

    graph = build_context_graph(repo, ledger)

    assert _edge(graph, "actor:nyx", "services/context-graph-service", "authored") is not None
    assert _edge(graph, capture_id, claim_node_id, "generated") is not None
    assert _edge(graph, claim_node_id, "services/context-graph-service", "supports") is not None
    assert _edge(graph, "trace:trace-context-graph", "plan:plan-context-graph", "generated") is not None
    assert _edge(graph, "plan:plan-context-graph", "services/context-graph-service", "applied") is not None
    assert _edge(graph, "plan:plan-context-graph", "policy:auto-apply:v1", "used-policy") is not None
    assert _edge(graph, "source:README.md", "services/context-graph-service", "source-of") is not None
    assert _edge(graph, "services/context-graph-service", "task:flow_000586", "task-related") is not None


def test_context_graph_deterministic(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)

    first = build_context_graph(repo, ledger)
    second = build_context_graph(repo, ledger)

    assert len(first.nodes) == len(second.nodes)
    assert len(first.edges) == len(second.edges)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_context_graph_stats(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)

    graph = build_context_graph(repo, ledger)

    assert graph.stats["page"] >= 2
    assert graph.stats["capture"] >= 1
    assert graph.stats["claim"] >= 1
    assert graph.stats["entity"] >= 1
    assert graph.stats["plan"] >= 1
    assert graph.stats["trace"] >= 1
    assert graph.stats["actor"] >= 1
    assert graph.stats["task"] >= 1
    assert graph.stats["policy"] >= 1
    assert graph.stats["source"] >= 1
    assert graph.stats["edges"] == len(graph.edges)


def test_context_graph_without_ledger(client):
    repo = client.app.state.repository
    _write_context_pages(repo)

    graph = build_context_graph(repo, ledger=None)

    assert _node(graph, "services/context-graph-service").type == "page"
    assert _node(graph, "decisions/context-graph-decision").type == "page"
    assert _node(graph, "plan:plan-context-graph") is None
    assert not [node for node in graph.nodes if node.id.startswith("candidate:")]


def test_neighbors_returns_connected_nodes(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(graph, ContextGraphNeighborQuery(node_id="services/context-graph-service", direction="both"))

    neighbor_ids = {neighbor.node.id for neighbor in result.neighbors}
    assert "actor:nyx" in neighbor_ids
    assert "source:README.md" in neighbor_ids
    assert "task:flow_000586" in neighbor_ids


def test_neighbors_direction_filter(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(graph, ContextGraphNeighborQuery(node_id="actor:nyx", direction="outgoing"))

    assert result.neighbors
    assert all(neighbor.edge.source == "actor:nyx" for neighbor in result.neighbors)


def test_neighbors_edge_type_filter(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(
        graph,
        ContextGraphNeighborQuery(node_id="services/context-graph-service", direction="both", edge_types=["authored"]),
    )

    assert result.neighbors
    assert all(neighbor.edge.type == "authored" for neighbor in result.neighbors)


def test_neighbors_node_type_filter(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(
        graph,
        ContextGraphNeighborQuery(node_id="actor:nyx", direction="outgoing", node_types=["page"]),
    )

    assert result.neighbors
    assert all(neighbor.node.type == "page" for neighbor in result.neighbors)


def test_paths_between_connected_nodes(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_paths(
        graph,
        ContextGraphPathQuery(source_id="actor:nyx", target_id="services/context-graph-service"),
    )

    assert result.paths
    assert any(path.length == 1 for path in result.paths)


def test_paths_multi_hop(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_paths(
        graph,
        ContextGraphPathQuery(source_id="actor:nyx", target_id="policy:auto-apply:v1", max_depth=3),
    )

    assert result.paths


def test_paths_missing_target(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_paths(
        graph,
        ContextGraphPathQuery(source_id="actor:nyx", target_id="nonexistent:node"),
    )

    assert result.paths == []


def test_explain_context_returns_neighborhood(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = explain_context(graph, ContextExplainQuery(node_id="services/context-graph-service", depth=1))

    assert "Context for page 'Context Graph Service'" in result.explanation
    assert "Total neighbors:" in result.explanation
    assert result.neighborhood


def test_explain_context_missing_node(client):
    repo, ledger, capture_id, candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = explain_context(graph, ContextExplainQuery(node_id="nonexistent:node"))

    assert "not found" in result.explanation


def test_mcp_context_graph_neighbors(client):
    _context_graph_fixture(client)

    response = _rpc(
        client,
        "lore_context_graph_neighbors",
        {"node_id": "services/context-graph-service", "direction": "both"},
    )

    assert response.status_code == 200
    payload = response.json()["result"]["structuredContent"]
    assert payload["neighbors"]


def test_mcp_explain_context(client):
    _context_graph_fixture(client)

    response = _rpc(
        client,
        "lore_explain_context",
        {"node_id": "services/context-graph-service", "depth": 1},
    )

    assert response.status_code == 200
    payload = response.json()["result"]["structuredContent"]
    assert payload["explanation"]
