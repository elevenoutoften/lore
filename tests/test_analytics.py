from __future__ import annotations

from lore_app.analytics import GraphAnalytics
from lore_app.schemas import ContextEdgeType, ContextGraph, ContextGraphEdge, ContextGraphNode, ContextNodeType


def _graph(node_ids: list[str], edges: list[tuple[str, str]]) -> ContextGraph:
    return ContextGraph(
        nodes=[ContextGraphNode(id=node_id, type=ContextNodeType.page, label=node_id) for node_id in node_ids],
        edges=[
            ContextGraphEdge(source=source, target=target, type=ContextEdgeType.mentions) for source, target in edges
        ],
    )


def test_degree_centrality_on_simple_graph():
    graph = _graph(["a", "b", "c"], [("a", "b"), ("b", "c")])

    result = GraphAnalytics(graph).compute()

    assert result.node_metrics["a"].degree_centrality == 0.5
    assert result.node_metrics["b"].degree_centrality == 1.0
    assert result.node_metrics["c"].degree_centrality == 0.5


def test_community_detection_on_clusters():
    graph = _graph(
        ["a", "b", "c", "d", "e", "f"],
        [("a", "b"), ("b", "c"), ("a", "c"), ("d", "e"), ("e", "f"), ("d", "f")],
    )

    result = GraphAnalytics(graph).compute()
    communities = [set(community) for community in result.communities]

    assert {"a", "b", "c"} in communities
    assert {"d", "e", "f"} in communities
    assert len(result.communities) == 2


def test_semantic_entry_points():
    graph = _graph(
        ["a", "b", "c", "bridge", "d", "e", "f"],
        [
            ("a", "b"),
            ("b", "c"),
            ("a", "c"),
            ("d", "e"),
            ("e", "f"),
            ("d", "f"),
            ("c", "bridge"),
            ("bridge", "d"),
        ],
    )

    entry_points = GraphAnalytics(graph).semantic_entry_points(k=3)

    assert "bridge" in entry_points


def test_analytics_caching():
    graph = _graph(["a", "b"], [("a", "b")])
    analytics = GraphAnalytics(graph)

    first = analytics.compute()
    second = analytics.compute()

    assert first is second


def test_empty_and_single_node_graphs_degrade_gracefully():
    empty = GraphAnalytics(ContextGraph()).compute()
    single = GraphAnalytics(_graph(["a"], [])).compute()

    assert empty.node_count == 0
    assert empty.node_metrics == {}
    assert single.node_count == 1
    assert single.node_metrics["a"].degree_centrality == 0.0
    assert single.node_metrics["a"].betweenness_centrality == 0.0


def test_api_graph_analytics_endpoint(client):
    response = client.get("/api/graph/analytics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_count"] > 0
    assert "node_metrics" in payload
    assert "communities" in payload
    assert "degree_centrality" in payload["top_nodes"]


def test_mcp_graph_analytics_tool(client):
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "lore_graph_analytics", "arguments": {}},
        },
        headers={"Mcp-Method": "tools/call"},
    )

    assert response.status_code == 200
    payload = response.json()["result"]["structuredContent"]
    assert payload["node_count"] > 0
    assert "betweenness" in payload["top_nodes"]
