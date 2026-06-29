from __future__ import annotations


def test_link_graph_api_reports_links_and_broken_links(client):
    response = client.get("/api/links")
    assert response.status_code == 200
    payload = response.json()

    assert [page["id"] for page in payload["pages"]] == [
        "procedures/add-lore-seed-page",
        "procedures/create-lore-capture",
        "procedures/deploy-lore-service",
        "projects/example-project",
        "services/workflow-engine",
    ]
    assert any(
        edge["source"] == "projects/example-project" and edge["target"] == "services/workflow-engine" and edge["exists"]
        for edge in payload["links"]
    )
    assert any(
        edge["source"] == "projects/example-project" and edge["target"] == "services/missing" and not edge["exists"]
        for edge in payload["broken_links"]
    )


def test_page_links_api_reports_backlinks(client):
    response = client.get("/api/pages/services/workflow-engine/links")
    assert response.status_code == 200
    payload = response.json()

    assert payload["page"]["id"] == "services/workflow-engine"
    assert any(edge["source"] == "projects/example-project" for edge in payload["backlinks"])
    assert any(edge["target"] == "projects/example-project" for edge in payload["outgoing"])


def test_reader_shows_relationship_panel(client):
    """The reader serves the SPA shell; its relationship panel (outgoing links +
    backlinks) is populated from the links API rather than server-rendered HTML."""
    response = client.get("/services/workflow-engine")
    assert response.status_code == 200
    assert 'id="loreRoot"' in response.text

    links = client.get("/api/pages/services/workflow-engine/links").json()
    assert any(edge["target"] == "projects/example-project" for edge in links["outgoing"])
    assert any(edge["source"] == "projects/example-project" for edge in links["backlinks"])


def test_graph_cache(client):
    """Graph cache returns consistent results."""
    first = client.get("/api/links")
    assert first.status_code == 200
    second = client.get("/api/links")
    assert second.status_code == 200
    assert first.json()["pages"] == second.json()["pages"]


def test_graph_cache_invalidates_on_write(client):
    """Cache invalidates when a page is created or deleted."""
    initial = client.get("/api/links").json()
    initial_count = len(initial["pages"])

    markdown = """---
title: Cache Test
kind: project
visibility: internal
---
# Cache Test
"""
    client.put("/api/pages/projects/cache-test", json={"content": markdown})
    after = client.get("/api/links").json()
    assert len(after["pages"]) == initial_count + 1

    client.delete("/api/pages/projects/cache-test")
    final = client.get("/api/links").json()
    assert len(final["pages"]) == initial_count


def test_graph_stats_endpoint(client):
    response = client.get("/api/graph/stats")
    assert response.status_code == 200
    payload = response.json()
    assert "pages" in payload
    assert "links" in payload
    assert "broken_links" in payload
    assert payload["pages"] >= 2


def test_enriched_graph_endpoint(client):
    response = client.get("/api/graph/enriched")
    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"]
    flow_node = next(n for n in payload["nodes"] if n["page_id"] == "services/workflow-engine")
    assert flow_node["inbound_count"] >= 1
    assert flow_node["kind"] == "service"
    assert flow_node["title"] == "Workflow Engine"


def test_source_edges_endpoint(client):
    response = client.get("/api/graph/sources")
    assert response.status_code == 200
    edges = response.json()
    assert isinstance(edges, list)
    # Our seed pages have sources
    if edges:
        assert edges[0]["relationship_type"] == "source_ref"


def test_link_edge_has_relationship_type(client):
    response = client.get("/api/links")
    payload = response.json()
    for edge in payload["links"]:
        assert "relationship_type" in edge
        assert edge["relationship_type"] == "wikilink"
