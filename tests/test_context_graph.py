from __future__ import annotations

from unittest import mock

from lore_app.context_graph import (
    ContextGraphCache,
    build_context_graph,
    explain_context,
    filter_context_graph,
    neighbors_of,
    query_neighbors,
    query_paths,
    truncate_context_graph,
)
from lore_app.schemas import (
    ContextExplainQuery,
    ContextGraph,
    ContextGraphNeighborQuery,
    ContextGraphPathQuery,
    ContextRef,
    ExtractedClaim,
    ExtractedEntity,
    ExtractionResult,
    PatchOperation,
    PatchPlan,
    PolicyDecision,
    PolicyRule,
    RiskLevel,
    ToolRef,
    TraceEntry,
)


def _node(graph, node_id: str):
    return next((node for node in graph.nodes if node.id == node_id), None)


def _edge(graph, source: str, target: str, edge_type: str):
    return next(
        (edge for edge in graph.edges if edge.source == source and edge.target == target and edge.type == edge_type),
        None,
    )


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
        headers={"X-Lore-Actor": "nyx"},
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
    entity_id = next(
        candidate["candidate_id"] for candidate in entities if candidate["batch_id"] == "batch-context-graph"
    )

    ledger.store_trace(
        TraceEntry(
            trace_id="trace-context-graph",
            actor="nyx",
            reason_summary="Trace for context graph test.",
            status="completed",
            context_refs=[
                ContextRef(type="page", id="services/context-graph-service"),
                ContextRef(type="candidate", id=claim_id),
            ],
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
            policies_applied=[
                PolicyDecision(policy_id="auto-apply:v1", gate="auto-apply", passed=True, reason="allowed")
            ],
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
    repo, ledger, _capture_id, candidate_ids = _context_graph_fixture(client)

    graph = build_context_graph(repo, ledger)

    assert _node(graph, f"candidate:{candidate_ids['claim_id']}").type == "claim"
    assert _node(graph, f"candidate:{candidate_ids['entity_id']}").type == "entity"


def test_candidate_nodes_include_bi_temporal_and_actor(client):
    repo = client.app.state.repository
    ledger = client.app.state.ledger_db

    repo.upsert_page("services/lore", "---\ntitle: Lore\nkind: service\n---\nLore service description.")
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="test-bi-temp",
            processed_at="2026-05-27T00:00:00+00:00",
            source_capture_ids=["inbox/test-bitemp"],
            claims=[
                ExtractedClaim(
                    subject="services/lore",
                    predicate="provides",
                    object="knowledge management",
                    confidence="high",
                    actor="agent:test-bot",
                    source_page_ids=["services/lore"],
                    observed_at="2026-05-27T12:00:00+00:00",
                    valid_from="2026-05-27T00:00:00+00:00",
                    valid_until="2026-12-31T23:59:59+00:00",
                )
            ],
        )
    )
    candidate = next(
        item
        for item in ledger.get_candidates(candidate_type="claim", status="candidate", limit=50)
        if item["batch_id"] == "test-bi-temp"
    )

    graph = build_context_graph(repo, ledger)
    node = _node(graph, f"candidate:{candidate['candidate_id']}")

    assert node is not None
    assert node.metadata.get("actor") == "agent:test-bot"
    assert node.metadata.get("observed_at") == "2026-05-27T12:00:00+00:00"
    assert node.metadata.get("valid_from") == "2026-05-27T00:00:00+00:00"
    assert node.metadata.get("valid_until") == "2026-12-31T23:59:59+00:00"


def test_page_nodes_include_observed_at_from_frontmatter(client):
    repo = client.app.state.repository
    ledger = client.app.state.ledger_db

    repo.upsert_page(
        "services/observed-service",
        "---\ntitle: Observed Service\nkind: service\nobserved_at: 2026-05-27T10:00:00+00:00\n---\nService description.",
    )

    graph = build_context_graph(repo, ledger)
    node = _node(graph, "services/observed-service")

    assert node is not None
    assert node.metadata["observed_at"] == "2026-05-27T10:00:00+00:00"


def test_context_graph_includes_plan_and_trace_nodes(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)

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


def test_rejected_claim_produces_no_supports_edge(client):
    repo, ledger, _capture_id, candidate_ids = _context_graph_fixture(client)
    claim_id = candidate_ids["claim_id"]
    claim_node_id = f"candidate:{claim_id}"

    before = build_context_graph(repo, ledger)
    assert _edge(before, claim_node_id, "services/context-graph-service", "supports") is not None

    ledger.reject_candidate(claim_id)
    graph = build_context_graph(repo, ledger)

    # A rejected claim is dead knowledge: no live node, no supports edge.
    assert _node(graph, claim_node_id) is None
    assert _edge(graph, claim_node_id, "services/context-graph-service", "supports") is None


def test_edge_to_missing_target_is_traversable_placeholder(client):
    repo = client.app.state.repository
    ledger = client.app.state.ledger_db
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-missing-target",
            entities=[],
            claims=[
                ExtractedClaim(
                    subject="Ghost",
                    predicate="references",
                    object="a missing page",
                    confidence="high",
                    source_page_ids=["ghosts/missing-page"],
                )
            ],
            edges=[],
            invalidations=[],
            source_capture_ids=["inbox/2026-05-01/ghost"],
            processed_at="2026-05-01T00:00:00+00:00",
        )
    )
    claims = ledger.get_candidates(
        candidate_type="claim", statuses=("candidate", "active"), limit=10000, max_rows=10000
    )
    claim_id = next(c["candidate_id"] for c in claims if c["batch_id"] == "batch-missing-target")
    claim_node_id = f"candidate:{claim_id}"

    graph = build_context_graph(repo, ledger)

    # The missing page endpoint is materialized as a placeholder node so the
    # supports edge is traversable rather than silently dropped.
    assert _node(graph, "ghosts/missing-page") is not None
    assert _edge(graph, claim_node_id, "ghosts/missing-page", "supports") is not None
    assert graph.stats["edges"] == len(graph.edges)

    reached = {neighbor.node.id for neighbor in neighbors_of(graph, claim_node_id, depth=1).neighbors}
    assert "ghosts/missing-page" in reached


def test_context_graph_deterministic(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)

    first = build_context_graph(repo, ledger)
    second = build_context_graph(repo, ledger)

    assert len(first.nodes) == len(second.nodes)
    assert len(first.edges) == len(second.edges)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_context_graph_cache_reuses_cached_graph(client, monkeypatch):
    repo = client.app.state.repository
    calls = 0

    def fake_build(repo_arg, ledger_arg=None):
        nonlocal calls
        calls += 1
        return ContextGraph()

    monkeypatch.setattr("lore_app.context_graph.build_context_graph", fake_build)
    cache = ContextGraphCache()

    first = cache.get(repo, None)
    second = cache.get(repo, None)

    assert first is second
    assert calls == 1


def test_context_graph_cache_invalidate_forces_rebuild(client, monkeypatch):
    repo = client.app.state.repository
    calls = 0

    def fake_build(repo_arg, ledger_arg=None):
        nonlocal calls
        calls += 1
        return ContextGraph(stats={"build": calls})

    monkeypatch.setattr("lore_app.context_graph.build_context_graph", fake_build)
    cache = ContextGraphCache()

    first = cache.get(repo, None)
    cache.invalidate()
    second = cache.get(repo, None)

    assert first is not second
    assert second.stats["build"] == 2
    assert calls == 2


def test_context_graph_api_uses_cache_for_identical_calls(client, monkeypatch):
    repo = client.app.state.repository
    _write_context_pages(repo)
    original_build = build_context_graph
    calls = 0

    def counted_build(repo_arg, ledger_arg=None):
        nonlocal calls
        calls += 1
        return original_build(repo_arg, ledger_arg)

    monkeypatch.setattr("lore_app.context_graph.build_context_graph", counted_build)

    first = client.get("/api/context-graph")
    second = client.get("/api/context-graph")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1


def test_context_graph_cache_no_ledger_reads_on_hit(client):
    """Repeated cache gets must not re-read ledger rows."""
    repo = client.app.state.repository
    repo.upsert_page("test-page", "---\ntitle: Test\n---\nBody text.")

    ledger = client.app.state.ledger_db
    cache = client.app.state.context_graph_cache
    cache.get(repo, ledger)

    with (
        mock.patch.object(ledger, "get_candidates", wraps=ledger.get_candidates) as mock_candidates,
        mock.patch.object(ledger, "list_traces", wraps=ledger.list_traces) as mock_traces,
        mock.patch.object(ledger, "list_patch_plans", wraps=ledger.list_patch_plans) as mock_plans,
        mock.patch.object(ledger, "list_policies", wraps=ledger.list_policies) as mock_policies,
    ):
        cache.get(repo, ledger)

    mock_candidates.assert_not_called()
    mock_traces.assert_not_called()
    mock_plans.assert_not_called()
    mock_policies.assert_not_called()


def test_context_graph_cache_invalidates_on_ledger_write(client, monkeypatch):
    """Cache should detect ledger writes via generation counter."""
    repo = client.app.state.repository
    repo.upsert_page("test-page", "---\ntitle: Test\n---\nBody text.")

    ledger = client.app.state.ledger_db
    cache = ContextGraphCache()
    original_build = build_context_graph
    calls = 0

    def counted_build(repo_arg, ledger_arg=None):
        nonlocal calls
        calls += 1
        return original_build(repo_arg, ledger_arg)

    monkeypatch.setattr("lore_app.context_graph.build_context_graph", counted_build)

    graph1 = cache.get(repo, ledger)
    gen1 = ledger.generation

    ledger.store_policy(
        PolicyRule(
            policy_id="test-policy:v1",
            name="Test policy",
            gate="auto-apply",
            condition_kind=[],
            condition_operation=[],
            effect_pass="allow",
            effect_fail="block",
            enabled=True,
        )
    )

    assert ledger.generation > gen1
    graph2 = cache.get(repo, ledger)

    assert graph2 is not graph1
    assert calls == 2


def test_context_graph_stats(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)

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


def test_filter_context_graph_pages_only(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)
    # Sanity: the full graph carries the typed memory nodes the client would drop.
    assert any(node.type.value != "page" for node in graph.nodes)

    pages_only = filter_context_graph(graph, ["page"])

    assert pages_only.nodes, "expected page nodes to survive the filter"
    assert all(node.type.value == "page" for node in pages_only.nodes)
    kept_ids = {node.id for node in pages_only.nodes}
    # Every retained edge must connect two retained (page) nodes.
    assert all(edge.source in kept_ids and edge.target in kept_ids for edge in pages_only.edges)
    # No claim/source/task/actor edges leak through the page-scoped payload.
    assert all(edge.type.value in {"mentions", "provenance"} for edge in pages_only.edges)
    assert pages_only.stats["page"] == len(pages_only.nodes)
    assert pages_only.stats["edges"] == len(pages_only.edges)
    assert "claim" not in pages_only.stats and "source" not in pages_only.stats


def test_filter_context_graph_empty_selection_is_noop(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    assert filter_context_graph(graph, None) is graph
    assert filter_context_graph(graph, []) is graph


def test_filter_then_truncate_reports_page_totals(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)
    pages_only = filter_context_graph(graph, ["page"])
    page_total = len(pages_only.nodes)
    assert page_total >= 2

    truncated = truncate_context_graph(pages_only, page_total - 1)

    # Truncation happens over the already-filtered page set, so total_nodes is the
    # page count (not the full typed-graph size) -- what the client header shows.
    assert truncated.stats["truncated"] == 1
    assert truncated.stats["total_nodes"] == page_total
    assert truncated.stats["returned_nodes"] == page_total - 1
    assert all(node.type.value == "page" for node in truncated.nodes)


def test_context_graph_api_node_types_page_only(client):
    _context_graph_fixture(client)

    response = client.get("/api/context-graph?node_types=page")

    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"], "expected page nodes in the scoped payload"
    assert all(node["type"] == "page" for node in payload["nodes"])
    assert "claim" not in payload["stats"]
    assert "source" not in payload["stats"]


def test_context_graph_without_ledger(client):
    repo = client.app.state.repository
    _write_context_pages(repo)

    graph = build_context_graph(repo, ledger=None)

    assert _node(graph, "services/context-graph-service").type == "page"
    assert _node(graph, "decisions/context-graph-decision").type == "page"
    assert _node(graph, "plan:plan-context-graph") is None
    assert not [node for node in graph.nodes if node.id.startswith("candidate:")]


def test_neighbors_returns_connected_nodes(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(
        graph, ContextGraphNeighborQuery(node_id="services/context-graph-service", direction="both")
    )

    neighbor_ids = {neighbor.node.id for neighbor in result.neighbors}
    assert "actor:nyx" in neighbor_ids
    assert "source:README.md" in neighbor_ids
    assert "task:flow_000586" in neighbor_ids


def test_neighbors_direction_filter(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(graph, ContextGraphNeighborQuery(node_id="actor:nyx", direction="outgoing"))

    assert result.neighbors
    assert all(neighbor.edge.source == "actor:nyx" for neighbor in result.neighbors)


def test_neighbors_edge_type_filter(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(
        graph,
        ContextGraphNeighborQuery(node_id="services/context-graph-service", direction="both", edge_types=["authored"]),
    )

    assert result.neighbors
    assert all(neighbor.edge.type == "authored" for neighbor in result.neighbors)


def test_neighbors_node_type_filter(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_neighbors(
        graph,
        ContextGraphNeighborQuery(node_id="actor:nyx", direction="outgoing", node_types=["page"]),
    )

    assert result.neighbors
    assert all(neighbor.node.type == "page" for neighbor in result.neighbors)


def test_paths_between_connected_nodes(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_paths(
        graph,
        ContextGraphPathQuery(source_id="actor:nyx", target_id="services/context-graph-service"),
    )

    assert result.paths
    assert any(path.length == 1 for path in result.paths)


def test_paths_multi_hop(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_paths(
        graph,
        ContextGraphPathQuery(source_id="actor:nyx", target_id="policy:auto-apply:v1", max_depth=3),
    )

    assert result.paths


def test_paths_missing_target(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = query_paths(
        graph,
        ContextGraphPathQuery(source_id="actor:nyx", target_id="nonexistent:node"),
    )

    assert result.paths == []


def test_explain_context_returns_neighborhood(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
    graph = build_context_graph(repo, ledger)

    result = explain_context(graph, ContextExplainQuery(node_id="services/context-graph-service", depth=1))

    assert "Context for page 'Context Graph Service'" in result.explanation
    assert "Total neighbors:" in result.explanation
    assert result.neighborhood


def test_explain_context_missing_node(client):
    repo, ledger, _capture_id, _candidate_ids = _context_graph_fixture(client)
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
