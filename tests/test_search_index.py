from __future__ import annotations

from lore_app.repository import LoreRepository
from lore_app.search_index import LoreSearchIndex


def create_lore_service_page(client):
    return client.put(
        "/api/pages/services/lore",
        json={
            "content": (
                "---\n"
                "title: Lore\n"
                "kind: service\n"
                "visibility: internal\n"
                "status: active\n"
                "tags: [lore, service]\n"
                "---\n\n"
                "# Lore\n\n"
                "Durable project memory for ExampleProject services."
            )
        },
    )


def test_search_index_rebuild(content_dir, tmp_path):
    """Rebuild indexes all pages."""
    repo = LoreRepository(content_dir)
    idx = LoreSearchIndex(tmp_path / "search.db")
    count = idx.rebuild(repo)
    assert count == 5
    idx.close()


def test_search_index_basic_query(content_dir, tmp_path):
    """Basic FTS query returns matching pages."""
    repo = LoreRepository(content_dir)
    idx = LoreSearchIndex(tmp_path / "search.db")
    idx.rebuild(repo)

    results = idx.search("ExampleProject")
    assert len(results) >= 1
    assert any(r["page_id"] == "projects/example-project" for r in results)
    idx.close()


def test_search_index_kind_filter(content_dir, tmp_path):
    """Kind filter limits results."""
    repo = LoreRepository(content_dir)
    idx = LoreSearchIndex(tmp_path / "search.db")
    idx.rebuild(repo)

    results = idx.search("GPU", kind="service")
    assert len(results) == 0
    idx.close()


def test_search_index_upsert_remove(content_dir, tmp_path):
    """Upsert and remove update the index."""
    repo = LoreRepository(content_dir)
    idx = LoreSearchIndex(tmp_path / "search.db")
    idx.rebuild(repo)

    repo.upsert_page("test/new-page", "---\ntitle: New Page\n---\n\n# New Page\n\nHello world")
    idx.upsert_page(repo._read_file(repo.page_path("test/new-page"), "test/new-page"))

    results = idx.search("Hello world")
    assert any(r["page_id"] == "test/new-page" for r in results)

    idx.remove_page("test/new-page")
    results = idx.search("Hello world")
    assert not any(r["page_id"] == "test/new-page" for r in results)

    idx.close()


def test_search_fts_api(client):
    """FTS search API endpoint works."""
    resp = client.post("/api/search/reindex")
    assert resp.status_code == 200
    assert resp.json()["indexed"] >= 2

    resp = client.get("/api/search/fts", params={"q": "ExampleProject"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "ExampleProject"
    assert len(data["hits"]) >= 1


def test_bm25_search_endpoint(client):
    """BM25 search endpoint returns ranked results with snippets."""
    client.post("/api/search/reindex")

    resp = client.get("/api/search/bm25", params={"q": "ExampleProject"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "ExampleProject"
    assert len(data["hits"]) >= 1

    hit = data["hits"][0]
    assert "page_id" in hit
    assert "title" in hit
    assert "score" in hit
    assert "snippet" in hit
    assert "matched_fields" in hit
    assert isinstance(hit["matched_fields"], list)
    assert hit["page_id"] == "projects/example-project"


def test_bm25_search_kind_filter(client):
    """BM25 search supports kind filtering."""
    client.post("/api/search/reindex")

    resp = client.get("/api/search/bm25", params={"q": "ExampleProject", "kind": "service"})
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert not any(h["page_id"] == "projects/example-project" for h in hits)


def test_bm25_search_body_match(client):
    """BM25 search matches body content."""
    client.post("/api/search/reindex")

    resp = client.get("/api/search/bm25", params={"q": "agent"})
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert len(hits) >= 1
    assert any("body" in h["matched_fields"] for h in hits)


def test_search_exact_page_id_ranks_first(client):
    """Exact page_id match ranks above partial matches."""
    client.post("/api/search/reindex")
    resp = create_lore_service_page(client)
    assert resp.status_code == 200

    resp = client.put(
        "/api/pages/test/lore-mention",
        json={
            "content": (
                "---\n"
                "title: Lore Mention Test\n"
                "---\n\n"
                "# Lore Mention Test\n\n"
                "The lore service is great. I love lore stuff."
            )
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/search/bm25", params={"q": "services/lore"})
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert len(hits) >= 1
    assert hits[0]["page_id"] == "services/lore"


def test_search_content_scores_preserve_relevance(client):
    """Content-match scores must not truncate to 0 (which forced alphabetical order)."""
    client.put(
        "/api/pages/test/quokka-frequent",
        json={"content": "---\ntitle: Quokka Frequent\n---\n\n# Quokka Frequent\n\n" + ("quokka " * 10)},
    )
    client.put(
        "/api/pages/test/quokka-rare",
        json={"content": "---\ntitle: Quokka Rare\n---\n\n# Quokka Rare\n\nquokka " + ("filler " * 9)},
    )
    client.post("/api/search/reindex")

    resp = client.get("/api/search", params={"q": "quokka", "limit": 10})
    assert resp.status_code == 200
    hits = [h for h in resp.json()["hits"] if h["page"]["id"].startswith("test/quokka-")]
    assert len(hits) == 2
    # The more frequent page ranks first and carries a non-zero (untruncated) score.
    assert hits[0]["page"]["id"] == "test/quokka-frequent"
    assert hits[0]["score"] > 0


def test_search_uses_frontmatter_validity_when_page_has_no_candidate(client):
    """Search provenance falls back to page frontmatter when no ledger candidate exists."""
    unique_term = "frontmatter-validity-token-1a2b3c"
    valid_from = "2026-05-01"
    valid_until = "2026-12-31"

    resp = client.put(
        "/api/pages/test/frontmatter-validity",
        json={
            "content": (
                "---\n"
                "title: Frontmatter Validity\n"
                f"valid_from: {valid_from}\n"
                f"valid_until: {valid_until}\n"
                "---\n\n"
                "# Frontmatter Validity\n\n"
                f"{unique_term}"
            )
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/search", params={"q": unique_term, "limit": 10})
    assert resp.status_code == 200
    hit = next(hit for hit in resp.json()["hits"] if hit["page"]["id"] == "test/frontmatter-validity")
    assert hit["valid_from"] == valid_from
    assert hit["valid_until"] == valid_until


def test_search_service_name_ranks_high(client):
    """Service name queries rank the matching service page high."""
    client.post("/api/search/reindex")
    resp = create_lore_service_page(client)
    assert resp.status_code == 200

    resp = client.get("/api/search/bm25", params={"q": "lore"})
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert len(hits) >= 2
    top_ids = [h["page_id"] for h in hits[:3]]
    assert "services/lore" in top_ids


def test_search_path_segment_matching(client):
    """Path queries match individual segments."""
    client.post("/api/search/reindex")

    resp = client.get("/api/search/bm25", params={"q": "example-project"})
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert any(h["page_id"] == "projects/example-project" for h in hits)


def test_search_index_invalidated_on_upsert(client):
    """PUT /api/pages updates the search index."""
    client.post("/api/search/reindex")

    resp = client.put(
        "/api/pages/test/brand-new",
        json={"content": "---\ntitle: Brand New\n---\n\n# Brand New\n\nUnique xyzzy content"},
    )
    assert resp.status_code == 200

    resp = client.get("/api/search/fts", params={"q": "xyzzy"})
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert any(h["page_id"] == "test/brand-new" for h in hits)


def test_search_index_invalidated_on_delete(client):
    """DELETE /api/pages removes from the search index."""
    client.post("/api/search/reindex")

    resp = client.put(
        "/api/pages/test/to-delete",
        json={"content": "---\ntitle: To Delete\n---\n\n# To Delete\n\nFleeting plugh content"},
    )
    assert resp.status_code == 200
    resp = client.get("/api/search/fts", params={"q": "plugh"})
    assert any(h["page_id"] == "test/to-delete" for h in resp.json()["hits"])

    resp = client.delete("/api/pages/test/to-delete")
    assert resp.status_code == 204

    resp = client.get("/api/search/fts", params={"q": "plugh"})
    assert not any(h["page_id"] == "test/to-delete" for h in resp.json()["hits"])


def test_search_index_invalidated_on_capture(client):
    """POST /api/capture updates the search index."""
    client.post("/api/search/reindex")

    resp = client.post(
        "/api/capture",
        json={
            "observation": "Captured xyzzy observation for index test.",
            "title": "Capture index test",
        },
    )
    assert resp.status_code == 201
    page_id = resp.json()["page"]["id"]

    resp = client.get("/api/search/fts", params={"q": "xyzzy"})
    assert any(h["page_id"] == page_id for h in resp.json()["hits"])
