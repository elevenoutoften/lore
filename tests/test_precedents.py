from __future__ import annotations

from lore_app.capture import capture_memory
from lore_app.schemas import CaptureRequest, ContextRef, ProvenanceRef, TraceEntry


def _rpc(client, name: str, arguments: dict):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        headers={"Mcp-Method": "tools/call"},
    )


def _seed_precedent_trace(client, **overrides) -> TraceEntry:
    payload = {
        "trace_id": overrides.pop("trace_id", "trace-routing"),
        "actor": "nyx",
        "reason_summary": "Resolved routing review after checking Lore routing history.",
        "status": "completed",
        "policy_refs": ["auto-apply:v1"],
        "outcome": "completed",
        "related_ids": {"task_id": "flow_000589"},
        "provenance": ProvenanceRef(task_ids=["flow_000589"], page_ids=["services/routing"], actor="nyx"),
        "context_refs": [ContextRef(type="page", id="services/routing")],
    }
    payload.update(overrides)
    trace = TraceEntry(**payload)
    return client.app.state.ledger_db.store_trace(trace)


def _seed_precedent_pages(client) -> None:
    repo = client.app.state.repository
    repo.upsert_page(
        "services/routing",
        """---
title: Routing Service
kind: service
visibility: internal
status: active
summary: Routing service history and prior fixes.
actor: nyx
lane: ops
policies_applied:
  - auto-apply:v1
task_id: flow_000589
---

# Routing Service

Routing changes were previously handled in Lore.
""",
    )
    repo.upsert_page(
        "decisions/routing-policy",
        """---
title: Routing Policy Decision
kind: decision
visibility: internal
status: accepted
summary: Accept routing auto-apply rules for Lore.
actor: nyx
policies_applied:
  - auto-apply:v1
source_task: flow_000589
---

# Routing Policy Decision

Lore accepted a routing policy decision.
""",
    )


def test_keyword_only_search(client):
    _seed_precedent_pages(client)
    _seed_precedent_trace(client)

    response = client.post("/api/precedents", json={"keyword": "routing", "limit": 10})

    assert response.status_code == 200, response.text
    body = response.json()
    ids = {match["id"] for match in body["matches"]}
    assert "services/routing" in ids
    assert "trace:trace-routing" in ids


def test_entity_and_policy_search(client):
    _seed_precedent_pages(client)
    _seed_precedent_trace(
        client,
        trace_id="trace-lore-policy",
        reason_summary="Lore applied auto-apply:v1 during a routing review.",
        related_ids={"task_id": "flow_policy"},
        provenance=ProvenanceRef(task_ids=["flow_policy"], page_ids=["services/routing"], actor="nyx"),
    )

    response = client.post("/api/precedents", json={"entity": "Lore", "policy": "auto-apply:v1", "limit": 10})

    assert response.status_code == 200, response.text
    body = response.json()
    assert any(match["id"] == "trace:trace-lore-policy" for match in body["matches"])


def test_task_ref_search(client):
    repo = client.app.state.repository
    capture = capture_memory(
        repo,
        CaptureRequest(
            title="Routing capture",
            observation="Track flow_000589 routing work.",
            actor="nyx",
            task_id="flow_000589",
            namespace="notes",
            capture_date="2026-05-22",
        ),
    )

    response = client.post("/api/precedents", json={"task_ref": "flow_000589", "limit": 10})

    assert response.status_code == 200, response.text
    body = response.json()
    match = next(item for item in body["matches"] if item["id"] == capture.id)
    assert match["type"] == "capture"


def test_no_results_graceful(client):
    response = client.post("/api/precedents", json={"keyword": "nonexistent_xyz_12345", "limit": 10})

    assert response.status_code == 200, response.text
    assert response.json() == {"matches": [], "total": 0}


def test_mcp_find_precedents(client):
    _seed_precedent_pages(client)
    _seed_precedent_trace(client)

    response = _rpc(client, "lore_find_precedents", {"keyword": "routing", "limit": 10})

    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    matches = payload["structuredContent"]["matches"]
    assert matches
    assert any(match["id"] == "trace:trace-routing" for match in matches)
