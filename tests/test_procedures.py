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