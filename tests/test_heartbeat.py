from __future__ import annotations

from datetime import datetime, timezone


def test_heartbeat_dashboard_includes_consolidation_section(client):
    response = client.get("/heartbeat")

    assert response.status_code == 200
    html = response.text
    assert "Consolidation" in html
    assert "/api/memory/health" in html
    assert "memory-health-last-consolidation" in html


def test_heartbeat_captures_creates_captures_for_stale_pages(client):
    created = client.put(
        "/api/pages/runbooks/stale-heartbeat-runbook",
        json={
            "content": """---
title: Stale Heartbeat Runbook
kind: runbook
visibility: internal
summary: A stale runbook used by heartbeat tests.
confidence: high
stale_after: 2020-01-01
---

# Stale Heartbeat Runbook
""",
        },
    )
    assert created.status_code == 200, created.text

    response = client.post("/api/heartbeat/captures")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["categories_covered"] == ["stale_pages"]
    assert len(payload["captures"]) == 1
    capture = payload["captures"][0]
    assert capture["id"].startswith(f"inbox/{datetime.now(timezone.utc).date().isoformat()}/")
    assert capture["title"] == "Heartbeat audit: 1 stale page"
    assert capture["kind"] == "capture"
    assert capture["status"] == "draft"
    assert capture["frontmatter"]["actor"] == "heartbeat"
    assert capture["frontmatter"]["lane"] == "ops"
    assert capture["frontmatter"]["confidence"] == "low"
    assert capture["frontmatter"]["epistemic_status"] == "hearsay"
    assert capture["frontmatter"]["source_task"] == "heartbeat-self-audit"


def test_heartbeat_captures_idempotent(client):
    created = client.put(
        "/api/pages/runbooks/idempotent-stale-runbook",
        json={
            "content": """---
title: Idempotent Stale Runbook
kind: runbook
visibility: internal
summary: A stale runbook used by heartbeat idempotency tests.
confidence: high
stale_after: 2020-01-01
---

# Idempotent Stale Runbook
""",
        },
    )
    assert created.status_code == 200, created.text

    first = client.post("/api/heartbeat/captures")
    second = client.post("/api/heartbeat/captures")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(first.json()["captures"]) == 1
    assert second.json()["captures"] == []
    assert "stale_pages" in second.json()["skipped_categories"]

    captures = client.get("/api/captures", params={"status": "all"}).json()["captures"]
    heartbeat_captures = [page for page in captures if page["title"].startswith("Heartbeat audit:")]
    assert len(heartbeat_captures) == 1


def test_heartbeat_captures_no_issues(client):
    response = client.post("/api/heartbeat/captures")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["captures"] == []
    assert payload["categories_covered"] == []
    assert set(payload["skipped_categories"]) == {
        "stale_pages",
        "contradictions",
        "low_confidence",
        "expired_facts",
        "procedure_issues",
    }


def test_heartbeat_captures_includes_observation_details(client):
    created = client.put(
        "/api/pages/services/heartbeat-observation-target",
        json={
            "content": """---
title: Heartbeat Observation Target
kind: service
visibility: internal
summary: A service with several heartbeat findings.
confidence: low
stale_after: 2020-01-01
valid_until: 2020-02-01
---

# Heartbeat Observation Target

CONTRADICTION: verify the service endpoint before relying on this page.
""",
        },
    )
    assert created.status_code == 200, created.text

    response = client.post("/api/heartbeat/captures")

    assert response.status_code == 200, response.text
    captures = {capture["title"]: capture for capture in response.json()["captures"]}
    assert "Heartbeat audit: 1 stale page" in captures
    assert "Heartbeat audit: 1 contradiction" in captures
    assert "Heartbeat audit: 1 low-confidence page" in captures
    assert "Heartbeat audit: 1 expired fact" in captures
    assert "services/heartbeat-observation-target" in captures["Heartbeat audit: 1 stale page"]["body"]
    assert "stale_after 2020-01-01" in captures["Heartbeat audit: 1 stale page"]["body"]
    assert "CONTRADICTION: verify the service endpoint" in captures["Heartbeat audit: 1 contradiction"]["body"]
    assert "valid_until 2020-02-01" in captures["Heartbeat audit: 1 expired fact"]["body"]


def test_heartbeat_captures_procedure_issues(client):
    """Heartbeat captures should include procedure_issue category."""
    client.put(
        "/api/pages/procedures/test-proc-no-steps",
        json={
            "content": '---\ntitle: No Steps Proc\nkind: procedure\nvisibility: internal\nsummary: Broken procedure\ntrigger: test\nsteps: []\nschema_version: "1.0"\nvalidated: false\nvalidated_at: null\nauthor: ""\n---\n\n# No Steps Proc\n',
        },
    )
    response = client.post("/api/heartbeat/captures")
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()["captures"]]
    assert any("procedure issue" in t.lower() or "procedure issue" in t for t in titles)


def test_heartbeat_captures_updates_search_index(client):
    """Heartbeat captures should appear in search after creation."""
    client.put(
        "/api/pages/runbooks/search-idx-heartbeat-runbook",
        json={
            "content": "---\ntitle: Search Idx Heartbeat Runbook\nkind: runbook\nvisibility: internal\nsummary: Tests search index update.\nconfidence: high\nstale_after: 2020-01-01\n---\n\n# Search Idx Heartbeat Runbook\n",
        },
    )
    response = client.post("/api/heartbeat/captures")
    assert response.status_code == 200
    assert len(response.json()["captures"]) == 1


def test_heartbeat_captures_writes_audit(client):
    """Heartbeat captures should create audit log entries."""
    client.put(
        "/api/pages/runbooks/audit-test-heartbeat-runbook",
        json={
            "content": "---\ntitle: Audit Test Heartbeat Runbook\nkind: runbook\nvisibility: internal\nsummary: Tests audit log.\nconfidence: high\nstale_after: 2020-01-01\n---\n\n# Audit Test Heartbeat Runbook\n",
        },
    )
    response = client.post("/api/heartbeat/captures")
    assert response.status_code == 200
    audit_resp = client.get("/api/audit", params={"limit": 20})
    assert audit_resp.status_code == 200
    entries = audit_resp.json()
    assert any(e.get("operation") == "heartbeat_capture" for e in entries)
