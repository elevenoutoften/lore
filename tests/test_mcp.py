from __future__ import annotations


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers={"Mcp-Method": method},
    )


def test_mcp_initialize_and_tool_list(client):
    initialized = rpc(client, "initialize")
    assert initialized.status_code == 200
    payload = initialized.json()
    assert payload["result"]["serverInfo"]["name"] == "lore"
    assert "tools" in payload["result"]["capabilities"]

    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "lore_list_pages",
        "lore_read_page",
        "lore_search",
        "lore_list_lanes",
        "lore_list_actors",
        "lore_rag_context",
        "lore_rag_context_expanded",
        "lore_link_graph",
        "lore_context_graph",
        "lore_graph_analytics",
        "lore_context_graph_neighbors",
        "lore_context_graph_paths",
        "lore_explain_context",
        "lore_page_links",
        "lore_lint",
        "lore_stale_pages",
        "lore_contradiction_review",
        "lore_frontmatter_spec",
        "lore_list_procedures",
        "lore_create_procedure",
        "lore_export_procedure",
        "lore_capture",
        "lore_list_captures",
        "lore_capture_digest",
        "lore_transition_capture",
        "lore_promote_capture",
        "lore_promotion_audit",
        "lore_create_stub",
        "lore_update_metadata",
        "lore_ingest_service",
        "lore_create_decision",
        "lore_create_trace",
        "lore_get_trace",
        "lore_get_provenance",
        "lore_list_traces",
        "lore_list_policies",
        "lore_find_precedents",
        "lore_get_policy",
        "lore_upsert_page",
        "lore_distill_daily",
        "lore_get_daily",
        "lore_promote_daily",
        "lore_heartbeat_review",
        "lore_heartbeat_summary",
        "lore_heartbeat_audit",
        "lore_find_repeated_captures",
        "lore_propose_procedure_candidate",
        "lore_consolidation_status",
        "lore_consolidation_run",
        "lore_consolidation_rollback",
        "lore_list_patch_plans",
        "lore_preview_patch",
        "lore_apply_patch",
        "lore_reject_patch",
        "lore_review_batch",
    ]


def test_mcp_search_read_and_upsert(client):
    searched = rpc(
        client,
        "tools/call",
        {"name": "lore_search", "arguments": {"query": "gateway service"}},
    ).json()
    assert searched["result"]["structuredContent"]["hits"][0]["page"]["id"] == "procedures/deploy-lore-service"

    read = rpc(
        client,
        "tools/call",
        {"name": "lore_read_page", "arguments": {"page_id": "projects/example-project"}},
    ).json()
    assert "# ExampleProject" in read["result"]["content"][0]["text"]

    upserted = rpc(
        client,
        "tools/call",
        {
            "name": "lore_upsert_page",
            "arguments": {
                "page_id": "runbooks/test",
                "content": "---\ntitle: Test Runbook\nkind: runbook\n---\n\n# Test Runbook\n",
            },
        },
    ).json()
    assert upserted["result"]["structuredContent"]["page"]["id"] == "runbooks/test"


def test_mcp_write_tools_are_rate_limited(client):
    from lore_app.security import RateLimiter

    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)

    first = rpc(
        client,
        "tools/call",
        {
            "name": "lore_upsert_page",
            "arguments": {
                "page_id": "runbooks/rate-one",
                "content": "---\ntitle: Rate One\nkind: runbook\n---\n\n# Rate One\n",
            },
        },
    )
    second = rpc(
        client,
        "tools/call",
        {
            "name": "lore_upsert_page",
            "arguments": {
                "page_id": "runbooks/rate-two",
                "content": "---\ntitle: Rate Two\nkind: runbook\n---\n\n# Rate Two\n",
            },
        },
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_write_tool_names_are_dispatchable_and_cover_page_writes():
    """WRITE_TOOL_NAMES must list only real tools and cover every page-mutating tool.

    Guards the L-SEC-11 default-deny rate limiter: a missing name silently
    unthrottles a write tool, and a dead name (no handler) is misleading cruft.
    """
    from lore_app.mcp.tools import TOOLS, WRITE_TOOL_NAMES

    tool_names = {tool["name"] for tool in TOOLS}
    assert WRITE_TOOL_NAMES <= tool_names, f"unknown tool names: {WRITE_TOOL_NAMES - tool_names}"

    page_mutating = {
        "lore_create_procedure",
        "lore_create_stub",
        "lore_update_metadata",
        "lore_transition_capture",
        "lore_distill_daily",
        "lore_promote_daily",
        "lore_upsert_page",
        "lore_capture",
    }
    assert page_mutating <= WRITE_TOOL_NAMES, f"unthrottled write tools: {page_mutating - WRITE_TOOL_NAMES}"


def test_mcp_batch_isolates_malformed_items(client):
    """A malformed batch member must not abort the whole batch (JSON-RPC 2.0)."""
    response = client.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            "not-an-object",
            {"jsonrpc": "1.0", "id": 3, "method": "tools/list"},
        ],
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 3
    by_id = {item.get("id"): item for item in payload}
    assert "result" in by_id[1]
    assert "error" in by_id[3]


def test_mcp_search_fts(client):
    """MCP lore_search uses FTS when available."""
    client.post("/api/search/reindex")

    resp = rpc(
        client,
        "tools/call",
        {
            "name": "lore_search",
            "arguments": {"query": "ExampleProject"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    content = data["result"]
    assert "structuredContent" in content
    hits = content["structuredContent"]["hits"]
    assert len(hits) >= 1
    assert any(h["page_id"] == "projects/example-project" for h in hits)
    assert "snippet" in hits[0]
    assert "matched_fields" in hits[0]


def test_mcp_rag_context(client):
    client.post("/api/search/reindex")

    resp = rpc(
        client,
        "tools/call",
        {
            "name": "lore_rag_context",
            "arguments": {"query": "gateway service gateway", "limit": 3},
        },
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]
    assert payload["isError"] is False
    results = payload["structuredContent"]["results"]
    assert results[0]["page_id"] == "procedures/create-lore-capture"
    assert "vector" in results[0]["sources"]
    assert "RAG result" in payload["content"][0]["text"]


def test_mcp_search_with_kind_filter(client):
    """MCP lore_search filters by kind."""
    client.post("/api/search/reindex")

    resp = rpc(
        client,
        "tools/call",
        {
            "name": "lore_search",
            "arguments": {"query": "GPU", "kind": "service"},
        },
    )
    assert resp.status_code == 200
    hits = resp.json()["result"]["structuredContent"]["hits"]
    assert not any(h["page_id"] == "projects/example-project" for h in hits)


def test_mcp_resources(client):
    resources = rpc(client, "resources/list").json()["result"]["resources"]
    assert resources[0]["uri"].startswith("lore://pages/")

    read = rpc(client, "resources/read", {"uri": "lore://pages/projects/example-project"}).json()
    assert read["result"]["contents"][0]["mimeType"] == "text/markdown"
    assert "ExampleProject" in read["result"]["contents"][0]["text"]


def test_mcp_link_graph_and_page_links(client):
    graph = rpc(client, "tools/call", {"name": "lore_link_graph", "arguments": {}}).json()
    assert graph["result"]["structuredContent"]["broken_links"][0]["target"] == "services/missing"

    links = rpc(
        client,
        "tools/call",
        {"name": "lore_page_links", "arguments": {"page_id": "services/workflow-engine"}},
    ).json()
    content = links["result"]["structuredContent"]
    assert content["page"]["id"] == "services/workflow-engine"
    assert any(edge["source"] == "projects/example-project" for edge in content["backlinks"])


def test_mcp_lint(client):
    lint = rpc(client, "tools/call", {"name": "lore_lint", "arguments": {}}).json()
    content = lint["result"]["structuredContent"]

    assert content["checked_pages"] == 5
    assert any(issue["rule"] == "broken_internal_link" for issue in content["issues"])
    assert "lint issues" in lint["result"]["content"][0]["text"]


def test_mcp_capture(client):
    captured = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "title": "Agent note",
                "observation": "Capture this as draft memory.",
                "namespace": "notes",
                "agent": "codex",
                "capture_date": "2026-05-01",
                "related_pages": ["services/workflow-engine"],
            },
        },
    ).json()

    page = captured["result"]["structuredContent"]["page"]
    assert page["id"] == "notes/codex/2026-05-01/agent-note"
    assert page["kind"] == "capture"
    assert "Captured Lore memory" in captured["result"]["content"][0]["text"]


def test_mcp_list_captures(client):
    rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "title": "Queue note",
                "observation": "This should appear in the draft capture queue.",
                "capture_date": "2026-05-01",
            },
        },
    )

    listed = rpc(client, "tools/call", {"name": "lore_list_captures", "arguments": {}}).json()
    content = listed["result"]["structuredContent"]

    assert content["count"] == 1
    assert content["captures"][0]["id"] == "inbox/2026-05-01/queue-note"
    assert "Lore captures" in listed["result"]["content"][0]["text"]


def test_mcp_notification_returns_accepted(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Method": "notifications/initialized"},
    )
    assert response.status_code == 202


def test_mcp_create_stub(client):
    result = rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_stub",
            "arguments": {
                "page_id": "services/mcp-stub-test",
                "title": "MCP Stub",
                "source_page": "projects/example-project",
            },
        },
    )
    assert result.status_code == 200
    payload = result.json()
    assert "Created Lore stub" in payload["result"]["content"][0]["text"]
    assert payload["result"]["structuredContent"]["page"]["id"] == "services/mcp-stub-test"
    assert payload["result"]["structuredContent"]["page"]["frontmatter"]["status"] == "stub"

    # Cleanup
    client.delete("/api/pages/services/mcp-stub-test")


def test_mcp_create_stub_already_exists(client):
    result = rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_stub",
            "arguments": {"page_id": "services/workflow-engine"},
        },
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["result"]["isError"] is True
    assert "already exists" in payload["result"]["content"][0]["text"]


def test_mcp_frontmatter_spec(client):
    result = rpc(client, "tools/call", {"name": "lore_frontmatter_spec", "arguments": {}})
    assert result.status_code == 200
    payload = result.json()["result"]

    assert payload["structuredContent"]["specs"]["capture"]["required"] == [
        "title",
        "kind",
        "visibility",
        "captured_at",
        "confidence",
        "agent",
    ]
    assert "frontmatter kind specs" in payload["content"][0]["text"]


def test_mcp_procedure_tools(client):
    listed = rpc(client, "tools/call", {"name": "lore_list_procedures", "arguments": {}}).json()["result"]
    procedures = listed["structuredContent"]["procedures"]
    assert len(procedures) == 3
    assert procedures[0]["page"]["kind"] == "procedure"
    assert procedures[0]["trigger"]
    assert procedures[0]["steps"]
    assert "Lore procedure" in listed["content"][0]["text"]

    created = rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_procedure",
            "arguments": {
                "title": "Review Lore Procedure",
                "summary": "Review a procedure for completeness.",
                "trigger": "Procedure changed",
                "steps": ["Read the procedure", "Run lint", "Commit updates"],
                "preconditions": ["Lore service is available"],
                "postconditions": ["Procedure is reviewed"],
                "error_handling": "Open a follow-up task.",
            },
        },
    ).json()["result"]
    page = created["structuredContent"]["page"]
    assert page["id"] == "procedures/review-lore-procedure"
    assert page["frontmatter"]["steps"] == ["Read the procedure", "Run lint", "Commit updates"]
    assert "Created Lore procedure" in created["content"][0]["text"]

    exported = rpc(
        client,
        "tools/call",
        {"name": "lore_export_procedure", "arguments": {"page_id": "procedures/review-lore-procedure"}},
    ).json()["result"]
    assert exported["structuredContent"]["page_id"] == "procedures/review-lore-procedure"
    assert "name: procedures-review-lore-procedure" in exported["structuredContent"]["content"]
    assert "1. Read the procedure" in exported["content"][0]["text"]


def test_mcp_update_metadata(client):
    result = rpc(
        client,
        "tools/call",
        {
            "name": "lore_update_metadata",
            "arguments": {
                "page_id": "projects/example-project",
                "owner": "platform",
                "reviewed_at": "2026-05-02",
                "stale_after": "2026-06-02",
                "confidence": "high",
                "status": "accepted",
            },
        },
    )
    assert result.status_code == 200
    page = result.json()["result"]["structuredContent"]["page"]

    assert page["frontmatter"]["owner"] == "platform"
    assert page["frontmatter"]["reviewed_at"] == "2026-05-02"
    assert page["frontmatter"]["stale_after"] == "2026-06-02"
    assert page["frontmatter"]["confidence"] == "high"
    assert page["frontmatter"]["status"] == "accepted"
    assert "ExampleProject runs compute, workflow, and knowledge services." in page["body"]


def test_mcp_create_decision(client):
    result = rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_decision",
            "arguments": {
                "title": "Use Lore Decisions",
                "summary": "Track choices as decision pages.",
                "context": "Choices need durable context.",
                "decision": "Use decision records.",
                "consequences": "Readers can list decisions by kind.",
                "deciders": ["codex", "team"],
                "status": "accepted",
            },
        },
    )
    assert result.status_code == 200
    payload = result.json()["result"]
    page = payload["structuredContent"]["page"]

    assert page["id"] == "decisions/use-lore-decisions"
    assert page["kind"] == "decision"
    assert page["frontmatter"]["deciders"] == ["codex", "team"]
    assert page["frontmatter"]["status"] == "accepted"
    assert "## Consequences" in page["content"]
    assert "Created Lore decision" in payload["content"][0]["text"]


def test_mcp_create_trace(client):
    result = rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_trace",
            "arguments": {
                "actor": "nyx",
                "reason_summary": "Selected low-risk patch over full rewrite.",
                "context_refs": [{"type": "page", "id": "services/lore"}],
                "related_ids": {"task_id": "flow_000581"},
            },
        },
    )

    assert result.status_code == 200
    trace = result.json()["result"]["structuredContent"]
    assert trace["trace_id"].startswith("trace-")
    assert trace["actor"] == "nyx"
    assert trace["context_refs"] == [{"type": "page", "id": "services/lore"}]


def test_mcp_get_trace(client):
    created = rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_trace",
            "arguments": {
                "actor": "nyx",
                "reason_summary": "Created trace for round-trip verification.",
            },
        },
    ).json()["result"]["structuredContent"]

    fetched = rpc(
        client,
        "tools/call",
        {"name": "lore_get_trace", "arguments": {"trace_id": created["trace_id"]}},
    )

    assert fetched.status_code == 200
    trace = fetched.json()["result"]["structuredContent"]
    assert trace["trace_id"] == created["trace_id"]
    assert trace["reason_summary"] == "Created trace for round-trip verification."


def test_mcp_list_traces(client):
    rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_trace",
            "arguments": {
                "actor": "mcp-list-nyx",
                "reason_summary": "Matching trace.",
                "related_ids": {"task_id": "flow_000581"},
            },
        },
    )
    rpc(
        client,
        "tools/call",
        {
            "name": "lore_create_trace",
            "arguments": {
                "actor": "codex",
                "reason_summary": "Non-matching trace.",
                "related_ids": {"task_id": "flow_000582"},
            },
        },
    )

    listed = rpc(
        client,
        "tools/call",
        {"name": "lore_list_traces", "arguments": {"actor": "mcp-list-nyx", "limit": 10}},
    )

    assert listed.status_code == 200
    content = listed.json()["result"]["structuredContent"]
    matching_traces = [
        trace
        for trace in content["traces"]
        if trace["actor"] == "mcp-list-nyx" and trace["related_ids"].get("task_id") == "flow_000581"
    ]
    assert content["total"] >= 1
    assert len(matching_traces) == 1


def test_mcp_unexpected_exception_redacts_message(client):
    """Unexpected exceptions return a generic message, not the raw exception string."""
    from unittest.mock import patch

    with patch(
        "lore_app.mcp.dispatch.handle_mcp_message",
        side_effect=RuntimeError("secret DB connection string at /var/lib/db"),
    ):
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "lore_list_pages", "arguments": {}},
            },
        )
    assert response.status_code == 500
    payload = response.json()
    assert "secret" not in payload["error"]["message"]
    assert "internal error" in payload["error"]["message"].lower()


def test_mcp_expected_jsonrpc_error_keeps_detail(client):
    """Expected JsonRpcError messages are surfaced to the client."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool"},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32601
    assert "Unsupported" in payload["error"]["message"]


def test_mcp_unexpected_exception_logs_traceback(client, caplog):
    """Unexpected exceptions are logged with traceback."""
    from unittest.mock import patch

    with patch("lore_app.mcp.dispatch.handle_mcp_message", side_effect=ValueError("oops")):
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/list",
                "params": {},
            },
        )
    assert response.status_code == 500
    assert any("Unexpected error in MCP handler" in record.message for record in caplog.records)
