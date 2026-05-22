from __future__ import annotations


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers={"Mcp-Method": method},
    )


def test_structured_metadata_round_trips_through_memory_capture_api(client):
    payload = {
        "text": "Structured metadata should round-trip through summary and detail surfaces.",
        "agent_name": "nyx",
        "task_id": "flow_000579",
        "decision_id": "decisions/memory-policy",
        "trace_id": "trace-roundtrip-api",
        "tool_calls": [{"tool": "search", "query": "memory metadata"}],
        "constraints": ["no-delete-without-review"],
        "policies_applied": ["L-MEM-03"],
        "metadata": {"title": "API structured metadata", "capture_date": "2026-05-22", "confidence": "high"},
    }

    captured = client.post("/api/memory/capture", json=payload)
    assert captured.status_code == 201, captured.text
    page_id = captured.json()["capture_id"]

    listed = client.get("/api/captures").json()
    summary = next(page for page in listed["captures"] if page["id"] == page_id)

    assert summary["source_task"] == "flow_000579"
    assert summary["decision_id"] == "decisions/memory-policy"
    assert summary["trace_id"] == "trace-roundtrip-api"
    assert summary["tool_calls"] == [{"tool": "search", "query": "memory metadata"}]
    assert summary["constraints"] == ["no-delete-without-review"]
    assert summary["policies_applied"] == ["L-MEM-03"]

    detail = client.get(f"/api/pages/{page_id}").json()
    assert detail["source_task"] == "flow_000579"
    assert detail["decision_id"] == "decisions/memory-policy"
    assert detail["trace_id"] == "trace-roundtrip-api"
    assert detail["tool_calls"] == [{"tool": "search", "query": "memory metadata"}]
    assert detail["constraints"] == ["no-delete-without-review"]
    assert detail["policies_applied"] == ["L-MEM-03"]
    assert detail["frontmatter"]["source_task"] == "flow_000579"
    assert detail["frontmatter"]["decision_id"] == "decisions/memory-policy"
    assert detail["frontmatter"]["trace_id"] == "trace-roundtrip-api"
    assert detail["frontmatter"]["tool_calls"] == [{"tool": "search", "query": "memory metadata"}]
    assert detail["frontmatter"]["constraints"] == ["no-delete-without-review"]
    assert detail["frontmatter"]["policies_applied"] == ["L-MEM-03"]


def test_structured_metadata_round_trips_through_mcp_capture(client):
    captured = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "title": "MCP structured metadata",
                "observation": "MCP capture should persist structured metadata.",
                "capture_date": "2026-05-22",
                "task_id": "flow_000580",
                "decision_id": "decisions/mcp-memory-policy",
                "trace_id": "trace-roundtrip-mcp",
                "tool_calls": [{"tool": "read", "path": "README.md"}],
                "constraints": ["preserve-frontmatter"],
                "policies_applied": ["L-MEM-03"],
            },
        },
    ).json()

    page = captured["result"]["structuredContent"]["page"]
    assert page["source_task"] == "flow_000580"
    assert page["decision_id"] == "decisions/mcp-memory-policy"
    assert page["trace_id"] == "trace-roundtrip-mcp"
    assert page["tool_calls"] == [{"tool": "read", "path": "README.md"}]
    assert page["constraints"] == ["preserve-frontmatter"]
    assert page["policies_applied"] == ["L-MEM-03"]

    listed = rpc(client, "tools/call", {"name": "lore_list_captures", "arguments": {}}).json()
    captures = listed["result"]["structuredContent"]["captures"]
    summary = next(capture for capture in captures if capture["id"] == page["id"])

    assert summary["source_task"] == "flow_000580"
    assert summary["decision_id"] == "decisions/mcp-memory-policy"
    assert summary["trace_id"] == "trace-roundtrip-mcp"
    assert summary["tool_calls"] == [{"tool": "read", "path": "README.md"}]
    assert summary["constraints"] == ["preserve-frontmatter"]
    assert summary["policies_applied"] == ["L-MEM-03"]
