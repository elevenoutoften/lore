from __future__ import annotations


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers={"Mcp-Method": method},
    )


def test_capture_provenance_refs_stored_and_retrieved(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "Provenance note",
            "observation": "Capture with explicit provenance refs.",
            "capture_date": "2026-05-01",
            "provenance": {
                "page_ids": ["services/workflow-engine"],
                "trace_ids": ["trace-explicit"],
                "task_ids": ["flow_000585"],
                "source_paths": ["lore_app/capture.py"],
            },
        },
    )

    assert response.status_code == 201, response.text
    page_id = response.json()["page"]["id"]
    readback = client.get(f"/api/pages/{page_id}")

    assert readback.status_code == 200, readback.text
    provenance = readback.json()["frontmatter"]["provenance"]
    assert provenance["page_ids"] == ["services/workflow-engine"]
    assert provenance["trace_ids"] == ["trace-explicit"]
    assert provenance["task_ids"] == ["flow_000585"]
    assert provenance["source_paths"] == ["lore_app/capture.py"]


def test_legacy_fields_merged_into_provenance(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "Legacy provenance",
            "observation": "Capture using legacy provenance fields.",
            "capture_date": "2026-05-01",
            "source_task": "flow_legacy",
            "source_paths": ["README.md"],
            "source_urls": ["https://example.com/source"],
            "task_id": "flow_000586",
            "decision_id": "decisions/provenance",
            "trace_id": "trace-legacy",
            "actor": "nyx",
            "related_pages": ["services/workflow-engine"],
            "tool_calls": [{"tool": "rg", "action": "search"}],
            "constraints": ["Keep legacy fields."],
            "policies_applied": ["trace-protocol"],
        },
        headers={"X-Lore-Actor": "nyx"},
    )

    assert response.status_code == 201, response.text
    page_id = response.json()["page"]["id"]
    provenance = client.get(f"/api/pages/{page_id}").json()["frontmatter"]["provenance"]
    assert provenance["source_task"] == "flow_legacy"
    assert provenance["source_paths"] == ["README.md"]
    assert provenance["source_urls"] == ["https://example.com/source"]
    assert provenance["task_ids"] == ["flow_000586", "decisions/provenance"]
    assert provenance["trace_ids"] == ["trace-legacy"]
    assert provenance["actor"] == "nyx"
    assert provenance["page_ids"] == ["services/workflow-engine"]
    assert provenance["tool_calls"] == [{"tool": "rg", "action": "search"}]
    assert provenance["constraints"] == ["Keep legacy fields."]
    assert provenance["policy_ids"] == ["trace-protocol"]


def test_trace_provenance_stored_and_retrieved(client):
    response = client.post(
        "/api/traces",
        json={
            "actor": "nyx",
            "reason_summary": "Trace with explicit provenance.",
            "provenance": {
                "page_ids": ["services/workflow-engine"],
                "capture_ids": ["inbox/2026-05-01/provenance-note"],
                "task_ids": ["flow_000585"],
                "source_paths": ["lore_app/ledger.py"],
            },
        },
    )

    assert response.status_code == 201, response.text
    created = response.json()
    readback = client.get(f"/api/traces/{created['trace_id']}")

    assert readback.status_code == 200, readback.text
    provenance = readback.json()["provenance"]
    assert provenance["page_ids"] == ["services/workflow-engine"]
    assert provenance["capture_ids"] == ["inbox/2026-05-01/provenance-note"]
    assert provenance["task_ids"] == ["flow_000585"]
    assert provenance["source_paths"] == ["lore_app/ledger.py"]


def test_provenance_lookup_for_capture(client):
    created = client.post(
        "/api/capture",
        json={
            "title": "Capture lookup",
            "observation": "Capture provenance lookup.",
            "capture_date": "2026-05-01",
            "provenance": {"page_ids": ["services/workflow-engine"], "trace_ids": ["trace-lookup"]},
        },
    ).json()["page"]

    response = client.get(f"/api/provenance/capture/{created['id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity_type"] == "capture"
    assert body["entity_id"] == created["id"]
    assert body["provenance"]["page_ids"] == ["services/workflow-engine"]
    assert body["provenance"]["trace_ids"] == ["trace-lookup"]


def test_provenance_lookup_for_trace(client):
    created = client.post(
        "/api/traces",
        json={
            "actor": "nyx",
            "reason_summary": "Trace provenance lookup.",
            "provenance": {"page_ids": ["services/workflow-engine"], "task_ids": ["flow_lookup"]},
        },
    ).json()

    response = client.get(f"/api/provenance/trace/{created['trace_id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity_type"] == "trace"
    assert body["entity_id"] == created["trace_id"]
    assert body["provenance"]["page_ids"] == ["services/workflow-engine"]
    assert body["provenance"]["task_ids"] == ["flow_lookup"]


def test_mcp_get_provenance(client):
    captured = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "title": "MCP provenance",
                "observation": "Capture provenance through MCP.",
                "capture_date": "2026-05-01",
                "provenance": {
                    "page_ids": ["services/workflow-engine"],
                    "task_ids": ["flow_mcp"],
                    "source_paths": ["lore_app/mcp/tools.py"],
                },
            },
        },
    ).json()["result"]["structuredContent"]["page"]

    response = rpc(
        client,
        "tools/call",
        {"name": "lore_get_provenance", "arguments": {"entity_type": "capture", "entity_id": captured["id"]}},
    )

    assert response.status_code == 200, response.text
    body = response.json()["result"]["structuredContent"]
    assert body["entity_type"] == "capture"
    assert body["entity_id"] == captured["id"]
    assert body["provenance"]["page_ids"] == ["services/workflow-engine"]
    assert body["provenance"]["task_ids"] == ["flow_mcp"]
    assert body["provenance"]["source_paths"] == ["lore_app/mcp/tools.py"]
