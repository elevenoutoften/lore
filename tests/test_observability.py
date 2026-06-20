from __future__ import annotations

import json


def test_healthz_returns_metrics(client):
    client.get("/api/pages")
    client.get("/api/search", params={"q": "ExampleProject"})
    client.post(
        "/api/capture",
        json={"title": "Metric capture", "observation": "Capture metrics should increment."},
    )

    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["metrics"]["request_count"] >= 3
    assert payload["metrics"]["pages_served"] >= 1
    assert payload["metrics"]["searches"] >= 1
    assert payload["metrics"]["captures"] >= 1
    assert payload["metrics"]["index_size"] >= 5
    assert "uptime_seconds" in payload["metrics"]


def test_metrics_endpoint_prometheus_text(client):
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for name in (
        "lore_consolidation_backlog",
        "lore_extraction_failures",
        "lore_apply_failures",
        "lore_recall_requests",
        "lore_recall_zero_results",
        "lore_client_error_count",
    ):
        assert f"# TYPE {name} " in body
        assert f"\n{name} " in body


def test_recall_increments_recall_metrics(client):
    # Empty ledger -> a zero-result recall.
    miss = client.get("/api/memory/recall")
    assert miss.status_code == 200
    assert miss.json()["count"] == 0

    metrics = client.get("/healthz").json()["metrics"]
    assert metrics["recall_requests"] >= 1
    assert metrics["recall_zero_results"] >= 1


def test_client_error_count_increments_on_4xx(client):
    not_found = client.get("/api/pages/does-not-exist")
    assert not_found.status_code == 404

    metrics = client.get("/healthz").json()["metrics"]
    assert metrics["client_error_count"] >= 1
    assert metrics["error_count"] == 0


def test_request_logging_is_structured(client, caplog):
    caplog.set_level("INFO", logger="lore")
    response = client.get("/api/pages", headers={"X-Lore-Actor": "agent:codex"})
    assert response.status_code == 200

    records = [json.loads(record.message) for record in caplog.records if record.name == "lore"]
    assert any(
        record["type"] == "request"
        and record["method"] == "GET"
        and record["path"] == "/api/pages"
        and record["status"] == 200
        and record["actor"] == "agent:codex"
        for record in records
    )
