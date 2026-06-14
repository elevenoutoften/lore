from __future__ import annotations


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers={"Mcp-Method": method},
    )


def test_capture_api_creates_draft_inbox_page(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "gateway service endpoint note",
            "observation": "The gateway service route should be checked before updating the runbook.",
            "capture_date": "2026-05-01",
            "source_task": "FLOW-123",
            "related_pages": ["services/workflow-engine"],
            "suggested_target_page": "projects/example-project",
            "confidence": "low",
            "sources": ["tests/test_capture.py"],
        },
    )

    assert response.status_code == 201, response.text
    page = response.json()["page"]
    assert page["id"] == "inbox/2026-05-01/gateway-service-endpoint-note"
    assert page["kind"] == "capture"
    assert page["status"] == "draft"
    assert page["frontmatter"]["confidence"] == "low"
    assert page["frontmatter"]["related"] == ["services/workflow-engine"]
    assert "Captured memory is rough intake" in page["content"]
    assert "[[services/workflow-engine]]" in page["content"]

    raw = client.get("/api/pages/inbox/2026-05-01/gateway-service-endpoint-note")
    assert raw.status_code == 200
    assert "FLOW-123" in raw.json()["content"]


def test_capture_api_merges_user_tags(client):
    """User-supplied tags must reach the stored capture frontmatter, not be dropped."""
    response = client.post(
        "/api/capture",
        json={
            "title": "Tagged capture",
            "observation": "A capture that carries user tags.",
            "capture_date": "2026-05-02",
            "tags": ["deploy", "gpu"],
        },
    )

    assert response.status_code == 201, response.text
    tags = response.json()["page"]["frontmatter"]["tags"]
    assert "deploy" in tags
    assert "gpu" in tags
    assert "capture" in tags


def test_capture_api_makes_duplicate_slugs_unique(client):
    payload = {
        "title": "Duplicate capture",
        "observation": "First version.",
        "capture_date": "2026-05-01",
    }
    first = client.post("/api/capture", json=payload)
    second = client.post("/api/capture", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["page"]["id"] == "inbox/2026-05-01/duplicate-capture"
    assert second.json()["page"]["id"] == "inbox/2026-05-01/duplicate-capture-2"


def test_capture_api_lists_draft_captures(client):
    draft = client.post(
        "/api/capture",
        json={
            "title": "Draft capture",
            "observation": "This should appear in the draft queue.",
            "capture_date": "2026-05-01",
        },
    )
    accepted = client.put(
        "/api/pages/inbox/2026-05-01/accepted-capture",
        json={
            "content": "---\ntitle: Accepted capture\nkind: capture\nvisibility: internal\nstatus: accepted\n---\n\n# Accepted\n",
        },
    )

    assert draft.status_code == 201
    assert accepted.status_code == 200

    drafts = client.get("/api/captures").json()
    assert drafts["status"] == "draft"
    assert drafts["count"] == 1
    assert drafts["captures"][0]["id"] == "inbox/2026-05-01/draft-capture"

    all_captures = client.get("/api/captures", params={"status": "all"}).json()
    assert all_captures["count"] == 2
    assert {page["status"] for page in all_captures["captures"]} == {"accepted", "draft"}


def test_capture_digest_empty(client):
    """Empty digest returns zero counts and empty groups."""
    resp = client.get("/api/captures/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_draft"] == 0
    assert data["total_review"] == 0
    assert data["by_date"] == []
    assert data["by_source_task"] == []
    assert data["by_suggested_target"] == []


def test_capture_digest_groups_captures(client):
    """Digest groups captures by date, source task, and target."""
    client.post(
        "/api/capture",
        json={
            "observation": "First observation.",
            "title": "Digest test one",
            "source_task": "task-alpha",
            "suggested_target_page": "services/lore",
        },
    )
    client.post(
        "/api/capture",
        json={
            "observation": "Second observation.",
            "title": "Digest test two",
            "source_task": "task-alpha",
            "suggested_target_page": "services/workflow-engine",
        },
    )

    resp = client.get("/api/captures/digest")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_draft"] == 2
    assert data["total_review"] == 0
    assert len(data["by_date"]) >= 1
    assert any(g["key"] == "task-alpha" and g["count"] == 2 for g in data["by_source_task"])

    targets = {g["key"] for g in data["by_suggested_target"]}
    assert "services/lore" in targets
    assert "services/workflow-engine" in targets


def test_capture_digest_mcp(client):
    """MCP lore_capture_digest tool returns structured digest."""
    client.post(
        "/api/capture",
        json={
            "observation": "MCP digest test.",
            "title": "MCP digest",
            "source_task": "flow_000113",
        },
    )

    resp = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture_digest",
            "arguments": {},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    assert "structuredContent" in data["result"]
    digest = data["result"]["structuredContent"]
    assert digest["total_draft"] >= 1


def test_capture_api_rejects_invalid_related_page(client):
    response = client.post(
        "/api/capture",
        json={
            "observation": "Invalid related page should fail.",
            "capture_date": "2026-05-01",
            "related_pages": ["../secrets"],
        },
    )

    assert response.status_code == 422


def test_capture_with_structured_sources(client):
    """Capture accepts source_paths, source_urls, and evidence fields."""
    resp = client.post(
        "/api/capture",
        json={
            "observation": "Lore service runs on port 8200.",
            "title": "Lore port",
            "source_paths": ["services/lore/lore_app/main.py"],
            "source_urls": ["https://fastapi.tiangolo.com/"],
            "evidence": "Verified by running ops/checks/test-lore-public.sh",
        },
    )
    assert resp.status_code == 201
    page = resp.json()["page"]
    fm = page["frontmatter"]
    assert "source_paths" in fm
    assert "services/lore/lore_app/main.py" in fm["source_paths"]
    assert "source_urls" in fm
    assert "https://fastapi.tiangolo.com/" in fm["source_urls"]
    assert fm.get("evidence") == "Verified by running ops/checks/test-lore-public.sh"


def test_capture_template_matches_docs(client):
    """Capture output matches the documented template conventions."""
    resp = client.post(
        "/api/capture",
        json={
            "observation": "Template convention verification.",
            "title": "Template test",
            "source_task": "flow_000114",
            "related_pages": ["services/lore"],
            "confidence": "high",
            "suggested_target_page": "services/lore",
            "sources": ["docs/capture-templates.md"],
            "source_paths": ["services/lore/lore_app/capture.py"],
            "source_urls": ["https://example.com"],
            "evidence": "Verified by reading source code",
        },
    )
    assert resp.status_code == 201
    page = resp.json()["page"]
    content = page["content"]
    fm = page["frontmatter"]

    assert content.startswith("---")
    assert fm["kind"] == "capture"
    assert fm["visibility"] == "internal"
    assert fm["status"] == "draft"
    assert "captured_at" in fm
    assert fm["confidence"] == "high"
    assert fm["source_task"] == "flow_000114"
    assert fm["suggested_target_page"] == "services/lore"
    assert "services/lore" in fm.get("related", [])
    assert "docs/capture-templates.md" in fm.get("sources", [])
    assert "services/lore/lore_app/capture.py" in fm.get("source_paths", [])
    assert "https://example.com" in fm.get("source_urls", [])
    assert fm.get("evidence") == "Verified by reading source code"

    assert "# Template test" in content
    assert "## Observation" in content
    assert "Template convention verification." in content


def test_capture_invalid_page_id_rejected(client):
    """Capture rejects clearly malformed page IDs in related_pages."""
    resp = client.post(
        "/api/capture",
        json={
            "observation": "Bad page ID test.",
            "related_pages": ["valid/page", "invalid page!@#"],
        },
    )
    assert resp.status_code == 422
    assert "Invalid page ID" in resp.json()["detail"]


def test_capture_invalid_source_url_rejected(client):
    """Capture rejects non-HTTP URLs in source_urls."""
    resp = client.post(
        "/api/capture",
        json={
            "observation": "Bad URL test.",
            "source_urls": ["ftp://example.com/bad"],
        },
    )
    assert resp.status_code == 422
    assert "Invalid source URL" in resp.json()["detail"]


def test_capture_status_transition_updates_frontmatter(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "Review candidate",
            "observation": "This capture should move to review.",
            "capture_date": "2026-05-01",
        },
    )
    assert captured.status_code == 201
    page_id = captured.json()["page"]["id"]

    transitioned = client.post(f"/api/captures/{page_id}/status", json={"status": "review"})

    assert transitioned.status_code == 200, transitioned.text
    page = transitioned.json()
    assert page["status"] == "review"
    assert page["frontmatter"]["status"] == "review"

    reread = client.get(f"/api/pages/{page_id}")
    assert reread.status_code == 200
    reread_page = reread.json()
    assert reread_page["frontmatter"]["status"] == "review"
    assert "status: review" in reread_page["content"]
    assert "This capture should move to review." in reread_page["content"]


def test_capture_status_transition_rejects_non_capture(client):
    created = client.put(
        "/api/pages/runbooks/not-a-capture",
        json={
            "content": "---\ntitle: Not a Capture\nkind: runbook\nvisibility: internal\nstatus: draft\n---\n\n# Not a Capture\n",
        },
    )
    assert created.status_code == 200

    transitioned = client.post("/api/captures/runbooks/not-a-capture/status", json={"status": "review"})

    assert transitioned.status_code == 422
    assert transitioned.json()["detail"] == "Only capture pages can be transitioned."


def test_capture_status_transition_rejects_invalid_status(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "Invalid status candidate",
            "observation": "This capture should reject invalid status.",
            "capture_date": "2026-05-01",
        },
    )
    assert captured.status_code == 201
    page_id = captured.json()["page"]["id"]

    transitioned = client.post(f"/api/captures/{page_id}/status", json={"status": "invalid"})

    assert transitioned.status_code == 422


def test_capture_promote_creates_new_page(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "Promoted service note",
            "observation": "This should become canonical service content.",
            "capture_date": "2026-05-01",
            "suggested_target_page": "services/test-promoted",
            "sources": ["tests/test_capture.py"],
        },
    )
    assert captured.status_code == 201
    page_id = captured.json()["page"]["id"]

    promoted = client.post(f"/api/captures/{page_id}/promote", json={})

    assert promoted.status_code == 200, promoted.text
    target = promoted.json()
    assert target["id"] == "services/test-promoted"
    assert target["kind"] == "service"
    assert target["status"] == "active"
    assert target["frontmatter"]["summary"] == f"Promoted from capture {page_id}"
    assert target["frontmatter"]["sources"] == [page_id]
    assert "This should become canonical service content." in target["content"]

    capture = client.get(f"/api/pages/{page_id}").json()
    assert capture["frontmatter"]["status"] == "accepted"
    assert capture["frontmatter"]["promoted_to"] == "services/test-promoted"
    assert "status: accepted\npromoted_to: services/test-promoted" in capture["content"]


def test_capture_promote_with_explicit_target(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "Explicit target note",
            "observation": "This should promote without suggested_target_page.",
            "capture_date": "2026-05-01",
        },
    )
    assert captured.status_code == 201
    page_id = captured.json()["page"]["id"]

    promoted = client.post(
        f"/api/captures/{page_id}/promote",
        json={"target_page_id": "runbooks/explicit-promoted"},
    )

    assert promoted.status_code == 200, promoted.text
    page = promoted.json()
    assert page["id"] == "runbooks/explicit-promoted"
    assert page["kind"] == "runbook"
    assert "This should promote without suggested_target_page." in page["content"]


def test_capture_promote_rejects_existing_without_content(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "Existing target note",
            "observation": "This should not silently overwrite.",
            "capture_date": "2026-05-01",
            "suggested_target_page": "services/existing-promoted",
        },
    )
    assert captured.status_code == 201
    page_id = captured.json()["page"]["id"]
    created = client.put(
        "/api/pages/services/existing-promoted",
        json={"content": "---\ntitle: Existing\nkind: service\n---\n\n# Existing\n"},
    )
    assert created.status_code == 200

    promoted = client.post(f"/api/captures/{page_id}/promote", json={})

    assert promoted.status_code == 422
    assert (
        promoted.json()["detail"]
        == "Target page already exists. Provide explicit content to overwrite, or choose a different target."
    )


def test_capture_promote_overwrites_with_content(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "Overwrite target note",
            "observation": "This body should not be used when explicit content is present.",
            "capture_date": "2026-05-01",
            "suggested_target_page": "services/overwrite-promoted",
        },
    )
    assert captured.status_code == 201
    page_id = captured.json()["page"]["id"]
    created = client.put(
        "/api/pages/services/overwrite-promoted",
        json={"content": "---\ntitle: Existing\nkind: service\n---\n\n# Existing\n"},
    )
    assert created.status_code == 200

    promoted = client.post(
        f"/api/captures/{page_id}/promote",
        json={
            "content": "---\ntitle: Overwritten\nkind: service\nvisibility: internal\nstatus: active\n---\n\n# Overwritten\n"
        },
    )

    assert promoted.status_code == 200, promoted.text
    page = promoted.json()
    assert page["title"] == "Overwritten"
    assert "# Overwritten" in page["content"]
    assert "This body should not be used" not in page["content"]

    capture = client.get(f"/api/pages/{page_id}").json()
    assert capture["frontmatter"]["status"] == "accepted"
    assert capture["frontmatter"]["promoted_to"] == "services/overwrite-promoted"


def test_promotion_audit_empty(client):
    """No promotions returns empty lists."""
    resp = client.get("/api/promotions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["promoted_captures"] == []
    assert data["pages_with_capture_sources"] == []


def test_promotion_audit_after_promotion(client):
    """After promoting a capture, audit trail reflects the relationship."""
    capture_resp = client.post(
        "/api/capture",
        json={
            "observation": "Lore has a FastAPI backend.",
            "title": "Lore backend stack",
            "suggested_target_page": "services/lore-stack",
        },
    )
    assert capture_resp.status_code == 201
    capture_id = capture_resp.json()["page"]["id"]

    promote_resp = client.post(
        f"/api/captures/{capture_id}/promote",
        json={
            "target_page_id": "services/lore-stack",
        },
    )
    assert promote_resp.status_code == 200

    audit_resp = client.get("/api/promotions")
    assert audit_resp.status_code == 200
    audit = audit_resp.json()

    assert len(audit["promoted_captures"]) == 1
    rec = audit["promoted_captures"][0]
    assert rec["capture_id"] == capture_id
    assert rec["target_page_id"] == "services/lore-stack"
    assert rec["capture_status"] == "accepted"

    assert len(audit["pages_with_capture_sources"]) == 1
    page = audit["pages_with_capture_sources"][0]
    assert page["page_id"] == "services/lore-stack"
    assert any(c["capture_id"] == capture_id for c in page["source_captures"])


def test_promotion_audit_mcp(client):
    """MCP lore_promotion_audit tool returns promotion trail."""
    capture_resp = client.post(
        "/api/capture",
        json={
            "observation": "Test capture for MCP audit.",
            "title": "MCP audit test",
            "suggested_target_page": "test/mcp-audit-target",
        },
    )
    capture_id = capture_resp.json()["page"]["id"]
    client.post(
        f"/api/captures/{capture_id}/promote",
        json={
            "target_page_id": "test/mcp-audit-target",
        },
    )

    resp = rpc(
        client,
        "tools/call",
        {
            "name": "lore_promotion_audit",
            "arguments": {},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    assert "structuredContent" in data["result"]
    audit = data["result"]["structuredContent"]
    assert len(audit["promoted_captures"]) >= 1


def test_capture_mcp_structured_sources(client):
    """MCP lore_capture tool accepts new source fields."""
    resp = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "observation": "MCP structured sources test.",
                "source_paths": ["src/main.py"],
                "source_urls": ["https://example.com/docs"],
                "evidence": "Seen in CI logs",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    content = data["result"]["structuredContent"]["page"]["frontmatter"]
    assert "source_paths" in content
    assert "source_urls" in content
    assert content.get("evidence") == "Seen in CI logs"


def test_mcp_capture_promote(client):
    captured = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "title": "MCP promote candidate",
                "observation": "This capture should promote through MCP.",
                "capture_date": "2026-05-01",
                "suggested_target_page": "services/mcp-promoted",
            },
        },
    ).json()
    page_id = captured["result"]["structuredContent"]["page"]["id"]

    promoted = rpc(
        client,
        "tools/call",
        {"name": "lore_promote_capture", "arguments": {"page_id": page_id}},
    ).json()

    page = promoted["result"]["structuredContent"]["page"]
    assert page["id"] == "services/mcp-promoted"
    assert "This capture should promote through MCP." in page["content"]
    assert f"Promoted capture to {page['id']}." in promoted["result"]["content"][0]["text"]

    capture = client.get(f"/api/pages/{page_id}").json()
    assert capture["frontmatter"]["status"] == "accepted"
    assert capture["frontmatter"]["promoted_to"] == "services/mcp-promoted"


def test_mcp_capture_transition(client):
    captured = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "title": "MCP transition candidate",
                "observation": "This capture should transition through MCP.",
                "capture_date": "2026-05-01",
            },
        },
    ).json()
    page_id = captured["result"]["structuredContent"]["page"]["id"]

    transitioned = rpc(
        client,
        "tools/call",
        {
            "name": "lore_transition_capture",
            "arguments": {"page_id": page_id, "status": "accepted"},
        },
    ).json()

    page = transitioned["result"]["structuredContent"]["page"]
    assert page["id"] == page_id
    assert page["status"] == "accepted"
    assert page["frontmatter"]["status"] == "accepted"
    assert f"Transitioned capture {page_id} to accepted." in transitioned["result"]["content"][0]["text"]


def test_promote_missing_capture_returns_404(client):
    """Promoting a non-existent capture is a 404, not a confusing 422."""
    resp = client.post("/api/captures/inbox/2026-01-01/does-not-exist/promote", json={})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
