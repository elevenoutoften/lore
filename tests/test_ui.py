from __future__ import annotations


def test_index_renders_page_list(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Lore" in response.text
    assert "ExampleProject" in response.text
    assert "Workflow Engine" in response.text
    assert 'href="/api-keys"' in response.text


def test_api_key_page_renders(client):
    response = client.get("/api-keys")
    assert response.status_code == 200
    assert "API Keys" in response.text
    assert "data-key-form" in response.text
    assert "/api/api-keys" in response.text


def test_settings_page_renders(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert "LLM Provider Settings" in response.text
    assert "data-settings-form" in response.text
    assert "/api/settings/llm" in response.text
    assert 'name="api_key" type="password"' in response.text
    assert 'name="escalation_api_key" type="password"' in response.text
    assert 'name="bearer_token" type="password"' in response.text


def test_page_view_renders_markdown(client):
    response = client.get("/projects/example-project")
    assert response.status_code == 200
    assert "ExampleProject runs compute" in response.text
    assert "<h2" in response.text
    assert "<table>" in response.text
    assert 'href="/services/workflow-engine"' in response.text
    assert "wiki-link--missing" in response.text
    assert "data-stub-target" in response.text


def test_rendered_api_returns_html_without_changing_raw_api(client):
    rendered = client.get("/api/pages/projects/example-project/rendered")
    assert rendered.status_code == 200
    payload = rendered.json()
    assert payload["id"] == "projects/example-project"
    assert "<table>" in payload["html"]
    assert payload["toc"][0]["title"] == "Services"
    assert payload["missing_links"][0]["page_id"] == "services/missing"

    raw = client.get("/api/pages/projects/example-project").json()
    assert "# ExampleProject" in raw["content"]
    assert "[[Workflow Engine|services/workflow-engine]]" in raw["content"]


def test_search_page(client):
    """GET /search returns BM25 search results."""
    client.post("/api/search/reindex")

    resp = client.get("/search", params={"q": "ExampleProject"})
    assert resp.status_code == 200
    assert "ExampleProject" in resp.text
    assert "projects/example-project" in resp.text


def test_search_page_with_kind(client):
    """GET /search supports kind filter."""
    client.post("/api/search/reindex")

    resp = client.get("/search", params={"q": "ExampleProject", "kind": "service"})
    assert resp.status_code == 200
    assert "projects/example-project" not in resp.text


def test_search_page_empty(client):
    """GET /search shows empty state for no results."""
    client.post("/api/search/reindex")

    resp = client.get("/search", params={"q": "zzzznonexistent"})
    assert resp.status_code == 200
    assert "No results" in resp.text or "0 result" in resp.text


def test_embed_page_route_renders_without_full_chrome(client):
    resp = client.get("/embed", params={"mode": "page", "pageId": "projects/example-project", "theme": "dark"})
    assert resp.status_code == 200
    assert "ExampleProject runs compute" in resp.text
    assert "data-lore-embed-root" in resp.text
    assert "topbar-links" not in resp.text
    assert "lore:resize" in resp.text


def test_embed_search_route(client):
    resp = client.get("/embed", params={"mode": "search", "q": "ExampleProject"})
    assert resp.status_code == 200
    assert "projects/example-project" in resp.text
    assert "Lore endpoints" not in resp.text


def test_lint_dashboard(client):
    """Lint dashboard renders and shows issues."""
    resp = client.get("/lint")
    assert resp.status_code == 200
    assert "Lint Dashboard" in resp.text


def test_graph_page(client):
    """GET /graph returns the graph visualization page."""
    resp = client.get("/graph")
    assert resp.status_code == 200
    assert "Link Graph" in resp.text
    assert "graphSvg" in resp.text
    assert "/api/graph/enriched" in resp.text
