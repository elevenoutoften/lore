from __future__ import annotations


def test_version_endpoint(client):
    response = client.get("/api/version")
    assert response.status_code == 200
    payload = response.json()
    assert {"name", "version", "python_version", "api_version"} <= payload.keys()
    assert payload["version"] == "0.3.0b1"


def test_list_read_and_catalog(client):
    pages = client.get("/api/pages").json()
    assert [page["id"] for page in pages] == [
        "procedures/add-lore-seed-page",
        "procedures/create-lore-capture",
        "procedures/deploy-lore-service",
        "projects/example-project",
        "services/workflow-engine",
    ]

    page = client.get("/api/pages/projects/example-project")
    assert page.status_code == 200
    body = page.json()
    assert body["title"] == "ExampleProject"
    assert body["kind"] == "project"
    assert body["tags"] == ["gpu", "cloud"]
    assert "workflow" in body["content"]

    catalog = client.get("/api/catalog").json()
    assert catalog["kinds"] == ["procedure", "project", "service"]
    assert catalog["visibilities"] == ["internal"]


def test_list_pages_supports_offset_and_total_header(client):
    response = client.get("/api/pages", params={"limit": 2, "offset": 1})
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "5"
    assert [page["id"] for page in response.json()] == [
        "procedures/create-lore-capture",
        "procedures/deploy-lore-service",
    ]


def test_search_scores_content_and_metadata(client):
    response = client.get("/api/search", params={"q": "ExampleProject computing services"})
    assert response.status_code == 200
    payload = response.json()
    assert any(h["page"]["id"] == "projects/example-project" for h in payload["hits"])
    assert payload["hits"][0]["score"] > 0
    assert payload["hits"][0]["matches"]


def test_search_hit_includes_provenance_fields(client):
    repo = client.app.state.repository
    repo.upsert_page(
        "services/prov-search",
        """---
title: Prov Search
kind: service
visibility: internal
observed_at: '2026-05-27T10:00:00+00:00'
actor: agent:test-bot
lane: project
source_paths:
  - docs/prov.md
source_urls:
  - https://example.com/prov
---

Prov search content with provenance.
""",
    )
    client.post("/api/search/reindex")

    response = client.get("/api/search", params={"q": "provenance"})
    assert response.status_code == 200
    hits = response.json()["hits"]
    found = [h for h in hits if h["page"]["id"] == "services/prov-search"]
    assert len(found) >= 1
    hit = found[0]
    assert hit.get("observed_at") == "2026-05-27T10:00:00+00:00"
    assert hit.get("actor") == "agent:test-bot"
    assert hit.get("lane") == "project"
    assert "docs/prov.md" in hit.get("source_refs", [])
    assert "https://example.com/prov" in hit.get("source_refs", [])


def test_upsert_and_delete_page(client):
    markdown = """---
title: Lore
kind: service
visibility: internal
status: active
---

# Lore

Markdown-backed project registry.
"""
    created = client.put("/api/pages/services/lore", json={"content": markdown})
    assert created.status_code == 200, created.text
    assert created.json()["id"] == "services/lore"
    assert created.json()["title"] == "Lore"

    listed = client.get("/api/pages", params={"kind": "service"}).json()
    assert [page["id"] for page in listed] == ["services/lore", "services/workflow-engine"]

    deleted = client.delete("/api/pages/services/lore")
    assert deleted.status_code == 204
    assert client.get("/api/pages/services/lore").status_code == 404


def test_rejects_unsafe_page_ids(client):
    assert client.get("/api/pages/../secrets").status_code in {404, 422}
    response = client.put("/api/pages/.hidden/page", json={"content": "# Hidden"})
    assert response.status_code == 422


def test_create_stub_page(client):
    response = client.post(
        "/api/pages/services/new-thing/stub",
        json={"title": "New Thing", "source_page": "projects/example-project"},
    )
    assert response.status_code == 201
    page = response.json()
    assert page["id"] == "services/new-thing"
    assert page["title"] == "New Thing"
    assert "stub" in page["content"]
    assert page["frontmatter"]["status"] == "stub"

    # Cleanup
    client.delete("/api/pages/services/new-thing")


def test_create_stub_conflict(client):
    # services/workflow-engine already exists
    response = client.post(
        "/api/pages/services/workflow-engine/stub",
        json={"title": "Workflow Engine"},
    )
    assert response.status_code == 409


def test_create_stub_default_title(client):
    response = client.post("/api/pages/runbooks/auto-titled/stub", json={})
    assert response.status_code == 201
    page = response.json()
    assert page["title"] == "Auto Titled"
    assert page["frontmatter"]["kind"] == "page"

    client.delete("/api/pages/runbooks/auto-titled")


def test_frontmatter_spec_endpoint(client):
    response = client.get("/api/frontmatter/spec")
    assert response.status_code == 200
    payload = response.json()

    assert set(payload["specs"]) == {"project", "service", "decision", "runbook", "concept", "capture", "procedure", "page"}
    assert payload["specs"]["service"]["required"] == ["title", "kind", "visibility", "summary", "owner"]
    assert payload["specs"]["procedure"]["required"] == [
        "title", "kind", "visibility", "summary", "trigger", "steps",
        "schema_version", "validated", "validated_at", "author",
    ]
    assert "sources" in payload["all_fields"]


def test_sources_display_in_reader(client):
    response = client.get("/projects/example-project")
    assert response.status_code == 200

    assert "provenance-panel" in response.text
    assert "Sources" in response.text
    assert "README.md" in response.text


def test_metadata_update(client):
    response = client.patch(
        "/api/pages/projects/example-project/metadata",
        json={
            "owner": "platform",
            "reviewed_at": "2026-05-02",
            "stale_after": "2026-06-02",
            "confidence": "high",
            "status": "accepted",
        },
    )
    assert response.status_code == 200, response.text
    page = response.json()

    assert page["frontmatter"]["owner"] == "platform"
    assert page["frontmatter"]["reviewed_at"] == "2026-05-02"
    assert page["frontmatter"]["stale_after"] == "2026-06-02"
    assert page["frontmatter"]["confidence"] == "high"
    assert page["frontmatter"]["status"] == "accepted"
    assert "ExampleProject runs compute, workflow, and knowledge services" in page["body"]


def test_decision_template(client):
    response = client.get("/api/decisions/template")
    assert response.status_code == 200
    content = response.json()["content"]

    assert "kind: decision" in content
    assert "## Context" in content
    assert "## Consequences" in content


def test_decision_listing(client):
    markdown = """---
title: Runtime Choice
kind: decision
visibility: internal
summary: Use the existing Lore runtime.
status: accepted
decided_at: "2026-05-02"
deciders:
  - codex
---

# Runtime Choice
"""
    created = client.put("/api/pages/decisions/runtime-choice", json={"content": markdown})
    assert created.status_code == 200

    response = client.get("/api/decisions")
    assert response.status_code == 200
    assert [page["id"] for page in response.json()] == ["decisions/runtime-choice"]


def test_procedure_template_listing_read_and_export(client):
    template = client.get("/api/procedures/template")
    assert template.status_code == 200
    assert "kind: procedure" in template.json()["content"]
    assert "## Error Handling" in template.json()["content"]

    listed = client.get("/api/procedures")
    assert listed.status_code == 200
    assert [page["id"] for page in listed.json()] == [
        "procedures/add-lore-seed-page",
        "procedures/create-lore-capture",
        "procedures/deploy-lore-service",
    ]

    page = client.get("/api/procedures/procedures/deploy-lore-service")
    assert page.status_code == 200
    assert page.json()["frontmatter"]["trigger"] == "Lore code changes merged to main on ExampleProject"

    export = client.get("/api/procedures/procedures/deploy-lore-service/export")
    assert export.status_code == 200
    content = export.json()["content"]
    assert "name: procedures-deploy-lore-service" in content
    assert "## Steps" in content
    assert "1. SSH to the server" in content


def test_procedure_read_rejects_non_procedure(client):
    assert client.get("/api/procedures/services/workflow-engine").status_code == 404
    assert client.get("/api/procedures/services/workflow-engine/export").status_code == 404


def test_ledger_candidates_endpoint(client):
    """Test GET /api/ledger/candidates returns typed candidates with full provenance."""
    from lore_app.schemas import ExtractedClaim, ExtractionResult

    # Insert a candidate via store_extraction_result (creates batch respecting FK)
    ledger = client.app.state.ledger_db
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="test-prov-batch",
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=["inbox/test-prov-cap"],
            claims=[
                ExtractedClaim(
                    subject="services/test-svc",
                    predicate="states",
                    object="Test service has a new API endpoint.",
                    confidence="high",
                    actor="nyx",
                    lane="project",
                    observed_at="2026-05-10T00:00:00+00:00",
                    valid_from="2026-05-01",
                    valid_until="2026-12-31",
                    evidence="Observed during deployment.",
                    source_page_ids=["captures/cap-001"],
                )
            ],
        )
    )

    # Query without filters — should return at least that candidate
    response = client.get("/api/ledger/candidates")
    assert response.status_code == 200
    candidates = response.json()
    assert isinstance(candidates, list)
    assert len(candidates) >= 1

    # Verify provenance fields on our inserted candidate
    found = [c for c in candidates if c["batch_id"] == "test-prov-batch"]
    assert len(found) == 1
    cand = found[0]
    assert cand["candidate_type"] == "claim"
    assert cand["status"] == "candidate"
    assert cand["confidence"] == "high"
    assert cand["actor"] == "nyx"
    assert cand["lane"] == "project"
    assert cand["observed_at"] == "2026-05-10T00:00:00+00:00"
    assert cand["valid_from"] == "2026-05-01"
    assert cand["valid_until"] == "2026-12-31"
    assert "captures/cap-001" in cand["source_page_ids"]
    assert "test-prov-cap" in cand["source_capture_ids"] or "inbox/test-prov-cap" in cand["source_capture_ids"]
    assert "created_at" in cand
    assert "updated_at" in cand

    # Test filter by actor
    filtered = client.get("/api/ledger/candidates", params={"actor": "nyx"})
    assert filtered.status_code == 200
    assert len(filtered.json()) >= 1

    filtered = client.get("/api/ledger/candidates", params={"actor": "nobody"})
    assert filtered.status_code == 200
    assert len(filtered.json()) == 0

    # Test filter by lane
    filtered = client.get("/api/ledger/candidates", params={"lane": "project"})
    assert filtered.status_code == 200
    assert len(filtered.json()) >= 1

    filtered = client.get("/api/ledger/candidates", params={"lane": "ops"})
    assert filtered.status_code == 200
    assert len(filtered.json()) == 0

    # Test filter by status
    filtered = client.get("/api/ledger/candidates", params={"status": "candidate"})
    assert filtered.status_code == 200
    assert len(filtered.json()) >= 1

    # Test filter by type
    filtered = client.get("/api/ledger/candidates", params={"type": "claim"})
    assert filtered.status_code == 200
    assert len(filtered.json()) >= 1

    # Test filter by page_id
    filtered = client.get("/api/ledger/candidates", params={"page_id": "captures/cap-001"})
    assert filtered.status_code == 200
    assert len(filtered.json()) >= 1


def test_semantics_endpoint(client):
    response = client.get("/api/semantics")
    assert response.status_code == 200
    payload = response.json()

    assert payload["confidence_levels"]["low"].startswith("Information is unverified")
    assert "accepted" in payload["status_values"]
    assert payload["visibility_levels"]["internal"].startswith("Visible")
