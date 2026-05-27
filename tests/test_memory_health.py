from __future__ import annotations

from fastapi.testclient import TestClient

from lore_app.config import LoreConfig
from lore_app.main import create_app


def make_client(tmp_path) -> TestClient:
    content_dir = tmp_path / "pages"
    content_dir.mkdir()
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = tmp_path / "search.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.vector_db = tmp_path / "vectors.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.trusted_headers = True
    return TestClient(create_app(config))


def test_memory_health_empty_state(tmp_path):
    with make_client(tmp_path) as client:
        response = client.get("/api/memory/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pending_captures"] == 0
    assert payload["review_required"] == 0
    assert payload["rejected_plans"] == 0
    assert payload["failed_runs"] == 0
    assert payload["last_consolidation"] is None
    assert payload["stale_pages"] == 0
    assert payload["contradictions"] == 0
    assert payload["low_confidence"] == 0
    assert payload["expired_facts"] == 0
    assert payload["missing_metadata"] == 0
    assert payload["procedure_issues"] == 0
    assert payload["total_issues"] == 0


def test_memory_health_with_captures_and_lint_signals(tmp_path):
    with make_client(tmp_path) as client:
        first = client.post(
            "/api/memory/capture",
            json={
                "text": "First draft capture for memory health coverage.",
                "agent_name": "tester",
                "metadata": {"title": "First health capture", "capture_date": "2026-05-01"},
            },
        )
        second = client.post(
            "/api/memory/capture",
            json={
                "text": "Second draft capture for memory health coverage.",
                "agent_name": "tester",
                "metadata": {"title": "Second health capture", "capture_date": "2026-05-01"},
            },
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text

        stale_page = client.put(
            "/api/pages/services/health-review-target",
            json={
                "content": """---
title: Health Review Target
kind: service
visibility: internal
sources:
  - tests/test_memory_health.py
stale_after: 2020-01-01
confidence: low
---

# Health Review Target

CONTRADICTION: this page needs a review pass.
""",
            },
        )
        assert stale_page.status_code == 200, stale_page.text

        response = client.get("/api/memory/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pending_captures"] >= 2
    assert payload["review_required"] == 0
    assert payload["rejected_plans"] == 0
    assert payload["last_consolidation"] is None
    assert payload["stale_pages"] >= 1
    assert payload["missing_metadata"] >= 1
    assert payload["low_confidence"] >= 1
    assert payload["contradictions"] >= 1
    assert payload["total_issues"] >= (
        payload["stale_pages"] + payload["missing_metadata"] + payload["low_confidence"] + payload["contradictions"]
    )
