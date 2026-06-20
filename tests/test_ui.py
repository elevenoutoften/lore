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


def test_search_page_works_without_manual_reindex(client):
    """The FTS index builds on startup, so the browser /search returns hits cold."""
    resp = client.get("/search", params={"q": "ExampleProject"})
    assert resp.status_code == 200
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
    """GET /graph returns the vis-network graph visualization page."""
    resp = client.get("/graph")
    assert resp.status_code == 200
    assert "Knowledge Graph" in resp.text
    assert "graphNetwork" in resp.text
    assert "/static/vendor/vis-network.min.js" in resp.text
    assert "/api/graph/enriched" in resp.text


def test_graph_layout_is_viewport_bounded(client):
    """Guard the L-GRAPH-01 blank-canvas regression at the CSS level.

    The canvas went blank because the shell used `min-height: 100vh` (an
    indefinite height) so the grid 1fr track never resolved and
    `#graphNetwork { height: 100% }` fell back to auto, letting vis-network grow
    its canvas to the full page height. The real rendered-dimension protection
    lives in tests/test_graph_browser.py; this is the cheap always-on guard that
    fails fast if the growth-prone CSS is reintroduced.
    """
    css = client.get("/graph").text
    # The shell must be pinned to a definite viewport height, not min-height:100vh.
    assert "100dvh" in css
    assert "min-height: 100vh" not in css
    # The network fills the bounded stage absolutely; the old growth combo
    # (height:100% + a tall min-height on #graphNetwork) must not return.
    assert "inset: 0" in css
    assert "min-height: 34rem" not in css


def test_empty_search_renders_gracefully(client):
    """An empty browser search renders the search page, not a raw JSON 422."""
    resp = client.get("/search")
    assert resp.status_code == 200
    assert "search" in resp.text.lower()


def test_missing_page_renders_themed_404(client):
    """A missing/mistyped wiki URL returns a styled 404 with nav + search, not raw JSON."""
    resp = client.get("/no/such/page-xyz")
    assert resp.status_code == 404
    assert "Page not found" in resp.text
    assert "topbar-links" in resp.text  # has the nav to get back
    assert "/search" in resp.text


def test_api_404_returns_json_not_html(client):
    """An unmatched /api/* path must give an SDK client a parseable JSON 404."""
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "<html" not in resp.text


def test_mcp_unmatched_path_returns_json_404(client):
    resp = client.get("/mcp/bogus")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "<html" not in resp.text


def test_nav_exposes_operator_pages(client):
    """The shared nav must make every operator page reachable by a click from /."""
    expected = ("/", "/graph", "/captures", "/procedures", "/heartbeat", "/lint", "/rag", "/api-keys", "/settings")
    home = client.get("/").text
    for href in expected:
        assert f'href="{href}"' in home, href
    # The nav is a shared partial, so a deep operator page carries the same links.
    assert 'href="/captures"' in client.get("/heartbeat").text
