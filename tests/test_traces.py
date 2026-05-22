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


def trace_payload(**overrides):
    payload = {
        "actor": "nyx",
        "reason_summary": "Selected the lower-risk patch after reviewing the target page.",
        "status": "active",
        "context_refs": [{"type": "page", "id": "services/lore"}],
        "tool_refs": [
            {
                "tool": "rg",
                "action": "search",
                "result_summary": "Found the existing route pattern.",
            }
        ],
        "constraints": ["Preserve existing API compatibility."],
        "policy_refs": ["trace-protocol"],
        "alternatives": [
            {
                "description": "Add trace fields to capture pages only.",
                "rejected_reason": "Would not support queryable audit records.",
            }
        ],
        "outcome": "Trace storage added.",
        "related_ids": {
            "task_id": "flow_000580",
            "page_id": "services/lore",
            "capture_id": "capture-123",
            "candidate_id": "candidate-123",
        },
    }
    payload.update(overrides)
    return payload


def test_create_trace_auto_generates_id_and_persists_fields(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post("/api/traces", json=trace_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["trace_id"].startswith("trace-")
    assert len(body["trace_id"]) == len("trace-") + 12
    assert body["actor"] == "nyx"
    assert body["context_refs"] == [{"type": "page", "id": "services/lore"}]
    assert body["tool_refs"][0]["result_summary"] == "Found the existing route pattern."
    assert body["constraints"] == ["Preserve existing API compatibility."]
    assert body["policy_refs"] == ["trace-protocol"]
    assert body["alternatives"][0]["rejected_reason"] == "Would not support queryable audit records."
    assert body["related_ids"]["task_id"] == "flow_000580"
    assert body["created_at"]
    assert body["updated_at"]


def test_get_trace_round_trips_all_fields(tmp_path):
    with make_client(tmp_path) as client:
        created = client.post("/api/traces", json=trace_payload()).json()
        response = client.get(f"/api/traces/{created['trace_id']}")

    assert response.status_code == 200, response.text
    assert response.json() == created


def test_list_traces_filters_by_actor_status_and_task_id(tmp_path):
    with make_client(tmp_path) as client:
        matching = client.post("/api/traces", json=trace_payload()).json()
        client.post(
            "/api/traces",
            json=trace_payload(
                actor="atlas",
                status="completed",
                related_ids={"task_id": "flow_other"},
            ),
        )

        by_actor = client.get("/api/traces", params={"actor": "nyx"}).json()
        by_status = client.get("/api/traces", params={"status": "active"}).json()
        by_task = client.get("/api/traces", params={"task_id": "flow_000580"}).json()

    assert by_actor["total"] == 1
    assert by_actor["traces"][0]["trace_id"] == matching["trace_id"]
    assert by_status["total"] == 1
    assert by_status["traces"][0]["trace_id"] == matching["trace_id"]
    assert by_task["total"] == 1
    assert by_task["traces"][0]["trace_id"] == matching["trace_id"]


def test_update_trace_changes_status_and_outcome(tmp_path):
    with make_client(tmp_path) as client:
        created = client.post("/api/traces", json=trace_payload(outcome="")).json()
        response = client.patch(
            f"/api/traces/{created['trace_id']}",
            json={"status": "completed", "outcome": "Decision recorded."},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trace_id"] == created["trace_id"]
    assert body["status"] == "completed"
    assert body["outcome"] == "Decision recorded."
    assert body["created_at"] == created["created_at"]
    assert body["updated_at"] >= created["updated_at"]


def test_get_or_patch_nonexistent_trace_returns_404(tmp_path):
    with make_client(tmp_path) as client:
        get_response = client.get("/api/traces/trace-missing")
        patch_response = client.patch("/api/traces/trace-missing", json={"status": "completed"})

    assert get_response.status_code == 404
    assert patch_response.status_code == 404


def test_trace_validation_rejects_empty_actor_or_reason_summary(tmp_path):
    with make_client(tmp_path) as client:
        empty_actor = client.post("/api/traces", json=trace_payload(actor=""))
        empty_summary = client.post("/api/traces", json=trace_payload(reason_summary=""))

    assert empty_actor.status_code == 422
    assert empty_summary.status_code == 422
