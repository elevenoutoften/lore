from __future__ import annotations

import json
from datetime import UTC, datetime

from lore_app.ledger import LedgerDB
from lore_app.schemas import ExtractedClaim, ExtractionResult


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
        "lore_extraction_tokens_total",
        "lore_extraction_tokens_last_batch",
        "lore_extraction_tokens_recent_average",
    ):
        assert f"# TYPE {name} " in body
        assert f"\n{name} " in body


def test_ledger_rolls_up_extraction_tokens_by_batch(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()

    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-1",
            processed_at=datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
            source_capture_ids=["capture-1", "capture-2"],
            claims=[
                ExtractedClaim(
                    subject="services/lore",
                    predicate="uses",
                    object="Auth",
                    confidence="high",
                    source_page_ids=["capture-1"],
                    token_usage={"prompt": 10, "completion": 5},
                ),
                ExtractedClaim(
                    subject="services/lore",
                    predicate="records",
                    object="Metrics",
                    confidence="high",
                    source_page_ids=["capture-1"],
                    token_usage={"prompt": 10, "completion": 5},
                ),
                ExtractedClaim(
                    subject="services/lore",
                    predicate="stores",
                    object="Claims",
                    confidence="high",
                    source_page_ids=["capture-2"],
                    token_usage={"prompt": 8, "completion": 4},
                ),
            ],
        )
    )
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-2",
            processed_at=datetime(2026, 5, 2, tzinfo=UTC).isoformat(),
            source_capture_ids=["capture-3"],
            claims=[
                ExtractedClaim(
                    subject="services/lore",
                    predicate="captures",
                    object="Token usage",
                    confidence="high",
                    source_page_ids=["capture-3"],
                    token_usage={"prompt": 6, "completion": 3},
                )
            ],
        )
    )

    rollup = ledger.get_extraction_token_usage_rollup()

    assert rollup["extraction_tokens_total"] == 36
    assert rollup["extraction_tokens_last_batch"] == 9
    assert rollup["extraction_tokens_recent_average"] == 18.0


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
