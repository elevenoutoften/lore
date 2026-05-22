from __future__ import annotations


PROCEDURE_MD = """---
title: Deploy Service
kind: procedure
visibility: internal
summary: Deploy a service to production.
trigger: When a new service version is ready for release
steps:
  - Run integration tests
  - Deploy to staging
  - Verify health checks
  - Deploy to production
preconditions:
  - All tests pass
postconditions:
  - Service is live
  - Monitoring dashboards show green
error_handling: Rollback via blue-green switch
schema_version: "1.0"
author: nyx
validated: true
validated_at: "2026-05-22T00:00:00+00:00"
---

# Deploy Service

1. Run integration tests
2. Deploy to staging
3. Verify health checks
4. Deploy to production
"""


def _create_procedure(client, page_id="procedures/deploy-service", **fm_overrides):
    import yaml

    fm = {
        "title": "Deploy Service",
        "kind": "procedure",
        "visibility": "internal",
        "summary": "Deploy a service to production.",
        "trigger": "When a new service version is ready for release",
        "steps": ["Run integration tests", "Deploy to staging", "Verify health checks", "Deploy to production"],
        "preconditions": ["All tests pass"],
        "postconditions": ["Service is live", "Monitoring dashboards show green"],
        "error_handling": "Rollback via blue-green switch",
        "schema_version": "1.0",
        "author": "nyx",
    }
    fm.update(fm_overrides)
    frontmatter_str = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
    body = "\n".join(f"{i}. {s}" for i, s in enumerate(fm["steps"], 1))
    content = f"---\n{frontmatter_str}\n---\n\n# {fm['title']}\n\n{body}\n"
    resp = client.put(f"/api/pages/{page_id}", json={"content": content})
    assert resp.status_code == 200, resp.text
    return page_id


def _create_capture(client, page_id, title):
    content = f"""---
title: {title}
kind: capture
visibility: internal
status: draft
---

# {title}

Repeated deployment rollback routing evidence for procedure candidate testing.
"""
    resp = client.put(f"/api/pages/{page_id}", json={"content": content})
    assert resp.status_code == 200, resp.text
    return page_id


def _create_candidate(client):
    capture_ids = [
        _create_capture(client, "inbox/candidate-capture-one", "Candidate Capture One"),
        _create_capture(client, "inbox/candidate-capture-two", "Candidate Capture Two"),
    ]
    resp = client.post(
        "/api/procedures/candidates",
        json={
            "capture_ids": capture_ids,
            "title": "Rollback Routing Procedure",
            "trigger": "When rollback routing appears repeatedly",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json(), capture_ids


def test_procedure_validation_endpoint(client):
    _create_procedure(client, "procedures/unvalidated-proc", validated=False)

    resp = client.post("/api/procedures/procedures/unvalidated-proc/validate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["validated"] is True
    assert body["validated_at"]

    page = client.get("/api/pages/procedures/unvalidated-proc").json()
    assert page["frontmatter"]["validated"] is True
    assert page["frontmatter"]["validated_at"]


def test_mcp_create_procedure_has_contract_fields(client):
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "lore_create_procedure",
                "arguments": {
                    "title": "Contract Test Proc",
                    "summary": "Test contract compliance",
                    "trigger": "When testing",
                    "steps": ["Step one", "Step two"],
                    "author": "test-agent",
                },
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    page = body["result"]["structuredContent"]["page"]
    fm = page["frontmatter"]
    assert fm["schema_version"] == "1.0"
    assert fm["validated"] is False
    assert fm["validated_at"] is None
    assert fm["author"] == "test-agent"


def test_procedure_template_has_contract_fields(client):
    resp = client.get("/api/frontmatter/spec")
    assert resp.status_code == 200
    spec = resp.json()["specs"]["procedure"]
    for field in ["schema_version", "validated", "validated_at", "author"]:
        assert field in spec["required"], f"{field} should be required for procedure"


def test_procedure_frontmatter_validation_catches_missing_contract(client):
    from lore_app.frontmatter_spec import validate_frontmatter

    incomplete = {
        "title": "Test",
        "kind": "procedure",
        "visibility": "internal",
        "summary": "test",
        "trigger": "test",
        "steps": ["step1"],
    }
    missing = validate_frontmatter("procedure", incomplete)
    for field in ["schema_version", "validated", "validated_at", "author"]:
        assert field in missing, f"{field} should be reported as missing"


def test_procedure_export_skill_format(client):
    _create_procedure(client)

    resp = client.post("/api/procedures/export", json={"page_id": "procedures/deploy-service", "format": "skill"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format"] == "skill"
    assert body["content"].startswith("---")
    assert "name: deploy-service" in body["content"]
    assert "## Steps" in body["content"]
    assert "Run integration tests" in body["content"]
    assert "## Preconditions" in body["content"]
    assert "## Postconditions" in body["content"]
    assert body["filename"] == "deploy-service.md"


def test_procedure_export_markdown_format(client):
    _create_procedure(client)

    resp = client.post("/api/procedures/export", json={"page_id": "procedures/deploy-service", "format": "markdown"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format"] == "markdown"
    assert "Deploy Service" in body["content"]


def test_procedure_lint_schema_version(client):
    _create_procedure(client, "procedures/no-schema-ver", schema_version=None)
    # Remove schema_version by recreating without it
    import yaml
    fm = {
        "title": "No Schema Ver",
        "kind": "procedure",
        "visibility": "internal",
        "summary": "Procedure without schema_version.",
        "trigger": "Manual trigger",
        "steps": ["Do something"],
    }
    frontmatter_str = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
    content = f"---\n{frontmatter_str}\n---\n\n# No Schema Ver\n\n1. Do something\n"
    resp = client.put("/api/pages/procedures/no-schema-ver", json={"content": content})
    assert resp.status_code == 200

    payload = client.get("/api/lint").json()
    rules = [i["rule"] for i in payload["issues"]]
    assert "procedure-schema-version" in rules


def test_procedure_lint_unvalidated(client):
    import yaml
    fm = {
        "title": "Unvalidated Proc",
        "kind": "procedure",
        "visibility": "internal",
        "summary": "Procedure not validated.",
        "trigger": "Manual trigger",
        "steps": ["Do something"],
        "schema_version": "1.0",
    }
    frontmatter_str = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
    content = f"---\n{frontmatter_str}\n---\n\n# Unvalidated Proc\n\n1. Do something\n"
    resp = client.put("/api/pages/procedures/unvalidated-proc-lint", json={"content": content})
    assert resp.status_code == 200

    payload = client.get("/api/lint").json()
    rules = [i["rule"] for i in payload["issues"]]
    assert "procedure-unvalidated" in rules


def test_procedure_export_not_procedure_422(client):
    client.put(
        "/api/pages/services/not-a-procedure",
        json={
            "content": "---\ntitle: Not A Procedure\nkind: service\nvisibility: internal\nsummary: test\n---\n\n# Not A Procedure\n"
        },
    )
    resp = client.post("/api/procedures/export", json={"page_id": "services/not-a-procedure", "format": "skill"})
    assert resp.status_code == 422


def test_candidate_creation_via_api(client):
    body, capture_ids = _create_candidate(client)

    assert body["page"]["id"].startswith("procedures/candidates/")
    assert len(body["source_captures"]) == 2
    assert body["message"]

    fm = body["page"]["frontmatter"]
    assert fm["kind"] == "procedure-candidate"
    assert fm["schema_version"] == "1.0"
    assert fm["validated"] is False
    assert fm["validated_at"] is None
    assert fm["source_capture_ids"] == capture_ids
    assert "sources" not in fm


def test_candidate_export_preserves_source_capture_ids(client):
    body, capture_ids = _create_candidate(client)
    page_id = body["page"]["id"]

    resp = client.post("/api/procedures/export", json={"page_id": page_id, "format": "skill"})
    assert resp.status_code == 200, resp.text
    content = resp.json()["content"]

    assert "## Source Captures" in content
    for capture_id in capture_ids:
        assert f"- {capture_id}" in content


def test_candidate_promotion_via_validate(client):
    body, _capture_ids = _create_candidate(client)
    page_id = body["page"]["id"]

    resp = client.post(f"/api/procedures/{page_id}/validate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["validated"] is True

    page = client.get(f"/api/pages/{page_id}").json()
    fm = page["frontmatter"]
    assert fm["validated"] is True
    assert fm["validated_at"]
    assert fm["schema_version"] == "1.0"
