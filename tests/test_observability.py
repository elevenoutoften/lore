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
