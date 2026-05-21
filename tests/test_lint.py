from __future__ import annotations

import json

from lore_app.lint import lint_lore
from lore_app.lint_config import LintConfig
from lore_app.repository import LoreRepository


def rpc(client, name):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}},
    )


def has_issue(payload, *, rule, page_id=None, target=None):
    return any(
        issue["rule"] == rule
        and (page_id is None or issue["page_id"] == page_id)
        and (target is None or issue.get("target") == target)
        for issue in payload["issues"]
    )


def test_lint_api_reports_broken_links_and_metadata(client):
    response = client.get("/api/lint")
    assert response.status_code == 200
    payload = response.json()

    assert payload["checked_pages"] == 5
    assert payload["issue_count"] == len(payload["issues"])
    assert has_issue(payload, rule="broken_internal_link", page_id="projects/example-project", target="services/missing")
    assert has_issue(payload, rule="missing_summary", page_id="services/workflow-engine")
    assert has_issue(payload, rule="missing_sources", page_id="services/workflow-engine")
    assert payload["suppressed_count"] == 0


def test_lint_issues_have_suggestions(client):
    """Lint issues include fix suggestions."""
    resp = client.get("/api/lint")
    payload = resp.json()
    issues_with_suggestions = [issue for issue in payload["issues"] if issue.get("suggestion")]
    assert len(issues_with_suggestions) > 0


def test_lint_fixable_endpoint(client):
    """/api/lint/fixable returns only auto-fixable issues."""
    resp = client.get("/api/lint/fixable")
    assert resp.status_code == 200
    data = resp.json()
    assert "fixable_count" in data
    assert "issues" in data
    for issue in data["issues"]:
        assert issue["auto_fixable"] is True
        assert issue["suggestion"] is not None


def test_lint_api_reports_review_risks(client):
    markdown = """---
title: Workflow Engine
kind: service
visibility: internal
summary: Duplicate title test page.
sources:
  - tests/test_lint.py
stale_after: 2020-01-01
confidence: low
---

# Workflow Engine Copy

CONTRADICTION: this should be reviewed before promotion.
"""
    created = client.put("/api/pages/services/workflow-copy", json={"content": markdown})
    assert created.status_code == 200

    payload = client.get("/api/lint").json()
    assert has_issue(payload, rule="duplicate_title", page_id="services/workflow-copy")
    assert has_issue(payload, rule="stale_page", page_id="services/workflow-copy")
    assert has_issue(payload, rule="low_confidence", page_id="services/workflow-copy")
    assert has_issue(payload, rule="contradiction_marker", page_id="services/workflow-copy")


def test_lint_config_disables_rule(content_dir, tmp_path):
    """Config can disable a lint rule."""
    config_path = tmp_path / ".lore-lint.json"
    config_path.write_text(json.dumps({"enabled_rules": ["broken_internal_link"]}), encoding="utf-8")
    config = LintConfig(config_path)
    repo = LoreRepository(content_dir)
    result = lint_lore(repo, config=config)

    for issue in result.issues:
        assert issue.rule == "broken_internal_link"


def test_lint_config_suppresses_issue(content_dir, tmp_path):
    """Config can suppress issues for a page."""
    config_path = tmp_path / ".lore-lint.json"
    config_path.write_text(
        json.dumps(
            {
                "suppressions": {
                    "services/workflow-engine": {"missing_summary": "Seed page, no summary needed"},
                }
            }
        ),
        encoding="utf-8",
    )
    config = LintConfig(config_path)
    repo = LoreRepository(content_dir)
    result = lint_lore(repo, config=config)

    assert result.suppressed_count == 1
    for issue in result.issues:
        if issue.page_id == "services/workflow-engine" and issue.rule == "missing_summary":
            assert issue.suppressed is True
            assert issue.suppression_reason == "Seed page, no summary needed"


def test_lint_config_severity_override(content_dir, tmp_path):
    """Config can override severity of a rule."""
    config_path = tmp_path / ".lore-lint.json"
    config_path.write_text(json.dumps({"severity_overrides": {"orphan_page": "warning"}}), encoding="utf-8")
    config = LintConfig(config_path)
    repo = LoreRepository(content_dir)
    result = lint_lore(repo, config=config)

    for issue in result.issues:
        if issue.rule == "orphan_page":
            assert issue.severity == "warning"


def test_orphan_suggestion(client):
    markdown = """---
title: Example GPU Capacity
kind: project
visibility: internal
summary: Capacity notes for GPU work.
tags: [gpu, cloud]
sources:
  - tests/test_lint.py
reviewed_at: 2026-05-01
---

# Example GPU Capacity
"""
    created = client.put("/api/pages/projects/example-gpu-capacity", json={"content": markdown})
    assert created.status_code == 200

    payload = client.get("/api/lint").json()
    issue = next(
        issue
        for issue in payload["issues"]
        if issue["rule"] == "orphan_page" and issue["page_id"] == "projects/example-gpu-capacity"
    )
    assert "Consider linking from:" in issue["suggestion"]
    assert "`projects/example-project`" in issue["suggestion"]
    assert issue["auto_fixable"] is False


def test_broken_link_suggestion(client):
    markdown = """---
title: Missing Service
kind: service
visibility: internal
summary: Placeholder for a similar service ID.
tags: [flow]
sources:
  - tests/test_lint.py
reviewed_at: 2026-05-01
---

# Missing Service
"""
    created = client.put("/api/pages/services/missing-service", json={"content": markdown})
    assert created.status_code == 200

    payload = client.get("/api/lint").json()
    issue = next(
        issue
        for issue in payload["issues"]
        if issue["rule"] == "broken_internal_link" and issue["target"] == "services/missing"
    )
    assert "Did you mean" in issue["suggestion"]
    assert "`services/missing-service`" in issue["suggestion"]
    assert issue["auto_fixable"] is False


def test_stale_queue(client):
    markdown = """---
title: Old Runbook
kind: runbook
visibility: internal
summary: Outdated review target.
sources:
  - tests/test_lint.py
stale_after: 2020-01-01
reviewed_at: 2019-12-01
---

# Old Runbook
"""
    created = client.put("/api/pages/runbooks/old-runbook", json={"content": markdown})
    assert created.status_code == 200

    response = client.get("/api/lint/stale")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 3
    assert payload["stale_pages"][0]["page_id"] == "runbooks/old-runbook"
    assert payload["stale_pages"][0]["severity"] == "stale"
    assert payload["stale_pages"][0]["days_stale"] > 0
    assert any(entry["page_id"] == "projects/example-project" for entry in payload["missing_metadata_pages"])
    assert any(entry["severity"] == "missing_metadata" for entry in payload["missing_metadata_pages"])


def test_contradiction_review(client):
    markdown = """---
title: Review Target
kind: runbook
visibility: internal
summary: Contradiction review target.
sources:
  - tests/test_lint.py
reviewed_at: 2026-05-01
---

# Review Target

Keep this first context line.
Keep this second context line.
CONFLICT: choose the current behavior.
Keep this following line.
"""
    created = client.put("/api/pages/runbooks/review-target", json={"content": markdown})
    assert created.status_code == 200

    response = client.get("/api/lint/contradictions")
    assert response.status_code == 200
    payload = response.json()
    match = next(item for item in payload["contradictions"] if item["page_id"] == "runbooks/review-target")
    assert payload["total_pages"] >= 1
    assert "CONFLICT:" in match["matched_text"]
    assert match["context_before"] == ["Keep this first context line.", "Keep this second context line."]
    assert match["context_after"] == ["Keep this following line."]


def test_contradiction_review_multiple_matches(client):
    markdown = """---
title: Multiple Markers
kind: runbook
visibility: internal
summary: Multiple contradiction markers.
sources:
  - tests/test_lint.py
reviewed_at: 2026-05-01
---

# Multiple Markers

CONTRADICTION: first marker.
Some neutral text.
TODO: verify second marker.
"""
    created = client.put("/api/pages/runbooks/multiple-markers", json={"content": markdown})
    assert created.status_code == 200

    payload = client.get("/api/lint/contradictions").json()
    matches = [item for item in payload["contradictions"] if item["page_id"] == "runbooks/multiple-markers"]
    assert len(matches) == 2
    assert matches[0]["line_number"] < matches[1]["line_number"]


def test_mcp_stale_pages(client):
    markdown = """---
title: MCP Stale
kind: runbook
visibility: internal
summary: Stale page for MCP.
sources:
  - tests/test_lint.py
stale_after: 2020-01-01
---

# MCP Stale
"""
    client.put("/api/pages/runbooks/mcp-stale", json={"content": markdown})

    payload = rpc(client, "lore_stale_pages").json()["result"]
    assert any(entry["page_id"] == "runbooks/mcp-stale" for entry in payload["structuredContent"]["stale_pages"])
    assert "freshness review" in payload["content"][0]["text"]


def test_mcp_contradiction_review(client):
    markdown = """---
title: MCP Contradiction
kind: runbook
visibility: internal
summary: Contradiction page for MCP.
sources:
  - tests/test_lint.py
reviewed_at: 2026-05-01
---

# MCP Contradiction

DISPUTED: needs owner review.
"""
    client.put("/api/pages/runbooks/mcp-contradiction", json={"content": markdown})

    payload = rpc(client, "lore_contradiction_review").json()["result"]
    markers = payload["structuredContent"]["contradictions"]
    assert any(marker["page_id"] == "runbooks/mcp-contradiction" for marker in markers)
    assert "contradiction marker" in payload["content"][0]["text"]


def test_lint_reports_procedure_quality_issues(client):
    markdown = """---
title: Broken Procedure
kind: procedure
visibility: internal
summary: Missing procedure metadata.
sources:
  - tests/test_lint.py
---

# Broken Procedure

## Steps
First do a thing.
"""
    created = client.put("/api/pages/procedures/broken-procedure", json={"content": markdown})
    assert created.status_code == 200

    payload = client.get("/api/lint").json()
    assert has_issue(payload, rule="procedure_missing_steps", page_id="procedures/broken-procedure")
    assert has_issue(payload, rule="procedure_missing_trigger", page_id="procedures/broken-procedure")
    missing_steps = next(
        issue
        for issue in payload["issues"]
        if issue["rule"] == "procedure_missing_steps" and issue["page_id"] == "procedures/broken-procedure"
    )
    assert missing_steps["suggestion"]
    assert missing_steps["auto_fixable"] is True


def test_lint_reports_procedure_steps_missing_from_body(client):
    markdown = """---
title: Body Mismatch Procedure
kind: procedure
visibility: internal
summary: Procedure with frontmatter/body mismatch.
trigger: Body needs checking
steps:
  - First frontmatter step
  - Second frontmatter step
sources:
  - tests/test_lint.py
---

# Body Mismatch Procedure

## Steps

1. First frontmatter step.
"""
    created = client.put("/api/pages/procedures/body-mismatch", json={"content": markdown})
    assert created.status_code == 200

    payload = client.get("/api/lint").json()
    issue = next(
        issue
        for issue in payload["issues"]
        if issue["rule"] == "procedure_step_not_in_body" and issue["page_id"] == "procedures/body-mismatch"
    )
    assert issue["detail"] == "Second frontmatter step"
    assert issue["suggestion"] == "Add the missing numbered step to the procedure body."
