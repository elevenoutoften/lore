from __future__ import annotations

import json

import pytest

ESTABLISHED_WRITE_TOOL_NAMES = frozenset(
    {
        "lore_ack_recall",
        "lore_apply_patch",
        "lore_capture",
        "lore_consolidation_rollback",
        "lore_consolidation_run",
        "lore_create_decision",
        "lore_create_procedure",
        "lore_create_stub",
        "lore_create_trace",
        "lore_distill_daily",
        "lore_heartbeat_audit",
        "lore_ingest_service",
        "lore_promote_capture",
        "lore_promote_daily",
        "lore_propose_procedure_candidate",
        "lore_reject_patch",
        "lore_retry_extraction_deadletter",
        "lore_transition_capture",
        "lore_update_metadata",
        "lore_upsert_page",
    }
)


def rpc(client, method, params=None, request_id=1, headers=None):
    request_headers = {"Mcp-Method": method}
    if headers:
        request_headers.update(headers)
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers=request_headers,
    )


def test_mcp_initialize_and_tool_list(client):
    initialized = rpc(client, "initialize")
    assert initialized.status_code == 200
    payload = initialized.json()
    assert payload["result"]["serverInfo"]["name"] == "lore"
    assert "tools" in payload["result"]["capabilities"]
    assert "lore_overview" in payload["result"]["instructions"]
    assert "six core tools only" in payload["result"]["instructions"]

    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "lore_capture",
        "lore_recall",
        "lore_ack_recall",
        "lore_search",
        "lore_read_page",
        "lore_upsert_page",
    ]


def test_lore_capture_schema_has_one_structured_server_safe_provenance_input(client):
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    capture = next(tool for tool in tools if tool["name"] == "lore_capture")
    properties = capture["inputSchema"]["properties"]

    assert "actor" not in properties
    assert set(properties).isdisjoint({"sources", "source_paths", "source_urls", "evidence"})
    provenance = properties["provenance"]
    assert provenance["type"] == "object"
    assert {"sources", "source_paths", "source_urls", "evidence"} <= set(provenance["properties"])
    assert "actor" not in provenance["properties"]


def test_mcp_default_tool_payload_is_at_least_80_percent_smaller(client):
    from lore_app.mcp.tools import TOOLS

    default_tools = rpc(client, "tools/list").json()["result"]["tools"]

    def compact(value):
        return len(json.dumps(value, separators=(",", ":")))

    assert compact(default_tools) <= compact(TOOLS) * 0.2


def test_mcp_search_read_and_upsert(client):
    searched = rpc(
        client,
        "tools/call",
        {"name": "lore_search", "arguments": {"query": "gateway service"}},
    ).json()
    # With the FTS index built (the normal case), lore_search returns flat FTS hits.
    hits = searched["result"]["structuredContent"]["hits"]
    assert any(h["page_id"] == "procedures/deploy-lore-service" for h in hits)

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


def test_mcp_retry_extraction_deadletter(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "MCP retry capture",
            "observation": "MCP retry references [[services/workflow-engine]].",
            "confidence": "high",
            "suggested_target_page": "services/lore",
        },
    )
    assert captured.status_code == 201, captured.text
    capture_id = captured.json()["page"]["id"]
    ledger = client.app.state.ledger_db
    deadletter_id = ledger.store_deadletter(
        capture_id=capture_id,
        provider="fallback",
        failure_kind="fallback_exhausted",
        failure_detail="previous failure",
        payload="{}",
        batch_id="batch-deadletter",
    )

    retried = rpc(
        client,
        "tools/call",
        {"name": "lore_retry_extraction_deadletter", "arguments": {"deadletter_id": deadletter_id}},
    )

    assert retried.status_code == 200, retried.text
    payload = retried.json()["result"]["structuredContent"]
    assert payload["deadletter_id"] == deadletter_id
    assert payload["capture_id"] == capture_id
    assert payload["retried"] is True
    assert payload["resolved"] is True
    assert payload["candidates"] > 0
    assert payload["source_capture_ids"] == [capture_id]


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


def test_annotations_drive_rate_limiting_for_every_registered_tool(client, monkeypatch):
    from lore_app.mcp.tools import READ_TOOL_NAMES, TOOL_HANDLERS, WRITE_TOOL_NAMES
    from lore_app.routes.mcp import mcp_write_call_count
    from lore_app.security import RateLimiter

    def fake_handler(_ctx):
        return {"content": [], "structuredContent": {}, "isError": False}

    for name in TOOL_HANDLERS:
        monkeypatch.setitem(TOOL_HANDLERS, name, fake_handler)

    batch = [
        {"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {"name": name, "arguments": {}}}
        for index, name in enumerate(sorted(WRITE_TOOL_NAMES), start=1)
    ]
    assert mcp_write_call_count(batch) == len(WRITE_TOOL_NAMES)

    for name in WRITE_TOOL_NAMES:
        client.app.state.write_rate_limiter = RateLimiter(max_requests=0, window_seconds=60)
        assert rpc(client, "tools/call", {"name": name, "arguments": {}}).status_code == 429

    client.app.state.write_rate_limiter = RateLimiter(max_requests=0, window_seconds=60)
    for name in READ_TOOL_NAMES:
        assert rpc(client, "tools/call", {"name": name, "arguments": {}}).status_code == 200

    assert rpc(client, "tools/call", {"name": "lore_not_registered", "arguments": {}}).status_code == 429


def test_tool_annotations_are_explicit_and_preserve_established_behavior():
    from lore_app.mcp.tools import (
        READ_TOOL_NAMES,
        TOOL_HANDLERS,
        TOOLS,
        WRITE_TOOL_NAMES,
        has_valid_tool_annotations,
        tool_access_mode,
    )

    tool_names = {tool["name"] for tool in TOOLS}
    assert len(tool_names) == len(TOOLS)
    assert tool_names == set(TOOL_HANDLERS)
    assert all(has_valid_tool_annotations(tool) for tool in TOOLS)
    assert tool_names == READ_TOOL_NAMES | WRITE_TOOL_NAMES
    assert READ_TOOL_NAMES.isdisjoint(WRITE_TOOL_NAMES)
    assert WRITE_TOOL_NAMES == ESTABLISHED_WRITE_TOOL_NAMES
    assert {tool["name"] for tool in TOOLS if tool_access_mode(tool) == "write"} == ESTABLISHED_WRITE_TOOL_NAMES


def test_mcp_classification_uses_annotations_and_fails_closed(monkeypatch):
    import lore_app.mcp.tools as tools_module
    from lore_app.routes.mcp import mcp_write_call_count

    def count(name):
        return mcp_write_call_count(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
        )

    assert count("lore_search") == 0
    search_tool = next(tool for tool in tools_module.TOOLS if tool["name"] == "lore_search")
    original_annotations = search_tool["annotations"]
    monkeypatch.setitem(search_tool, "annotations", {"readOnlyHint": False, "destructiveHint": True})
    assert count("lore_search") == 1
    monkeypatch.setitem(search_tool, "annotations", original_annotations)

    for invalid in (
        None,
        {},
        {"readOnlyHint": True},
        {"readOnlyHint": "true", "destructiveHint": False},
        {"readOnlyHint": True, "destructiveHint": True},
        {"readOnlyHint": False, "destructiveHint": False},
    ):
        monkeypatch.setitem(search_tool, "annotations", invalid)
        assert count("lore_search") == 1
    monkeypatch.setitem(search_tool, "annotations", original_annotations)

    monkeypatch.setattr(tools_module, "TOOLS", [*tools_module.TOOLS, dict(search_tool)])
    assert count("lore_search") == 1
    assert count("lore_not_registered") == 1


def test_registry_validation_rejects_handler_drift(monkeypatch):
    from lore_app.mcp.tools import TOOL_HANDLERS, validate_tool_registry

    monkeypatch.delitem(TOOL_HANDLERS, "lore_search")
    with pytest.raises(RuntimeError, match="registry and handlers"):
        validate_tool_registry()


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
    capture_result = next(result for result in results if result["page_id"] == "procedures/create-lore-capture")
    assert "vector" in capture_result["sources"]
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
        headers={"X-Lore-Actor": "codex"},
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


def _call(client, name, arguments):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments}).json()["result"]


def test_mcp_list_traces_pagination_offset(client):
    for i in range(3):
        _call(client, "lore_create_trace", {"actor": "mcp-page-nyx", "reason_summary": f"Trace {i}."})

    page1 = _call(client, "lore_list_traces", {"actor": "mcp-page-nyx", "limit": 2, "offset": 0})["structuredContent"]
    assert page1["total"] >= 3
    assert len(page1["traces"]) == 2
    assert page1["offset"] == 0
    assert page1["has_more"] is True

    page2 = _call(client, "lore_list_traces", {"actor": "mcp-page-nyx", "limit": 2, "offset": 2})["structuredContent"]
    assert page2["offset"] == 2
    page1_ids = {trace["trace_id"] for trace in page1["traces"]}
    page2_ids = {trace["trace_id"] for trace in page2["traces"]}
    assert page1_ids.isdisjoint(page2_ids)
    assert page2["has_more"] is False


def test_mcp_recall_pagination_offset(client):
    from lore_app.schemas import ExtractedClaim, ExtractionResult

    client.app.state.ledger_db.store_extraction_result(
        ExtractionResult(
            batch_id="batch-mcp-recall-page",
            processed_at="2026-05-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-01/recall-page"],
            claims=[
                ExtractedClaim(subject=f"services/r{i}", predicate="states", object=f"fact {i}", confidence="high")
                for i in range(3)
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )

    page1 = _call(client, "lore_recall", {"limit": 1, "offset": 0})["structuredContent"]
    assert page1["offset"] == 0
    assert page1["has_more"] is True
    assert len(page1["claims"]) == 1

    page2 = _call(client, "lore_recall", {"limit": 1, "offset": 1})["structuredContent"]
    assert page2["claims"][0]["candidate_id"] != page1["claims"][0]["candidate_id"]


def test_mcp_recall_has_more_with_record_access(client):
    from lore_app.schemas import ExtractedClaim, ExtractionResult

    client.app.state.ledger_db.store_extraction_result(
        ExtractionResult(
            batch_id="batch-mcp-recall-record-access",
            processed_at="2026-05-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-01/recall-ra"],
            claims=[
                ExtractedClaim(subject=f"services/ra{i}", predicate="states", object=f"fact {i}", confidence="high")
                for i in range(3)
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )

    # has_more must be correct even when record_access=True (the bounced defect:
    # the over-fetch probe used to be disabled on that path, so has_more was
    # always False and an agent could never page).
    page1 = _call(client, "lore_recall", {"limit": 1, "offset": 0, "record_access": True})["structuredContent"]
    assert page1["has_more"] is True
    assert len(page1["claims"]) == 1
    recalled_id = page1["claims"][0]["candidate_id"]

    last = _call(client, "lore_recall", {"limit": 1, "offset": 2, "record_access": True})["structuredContent"]
    assert last["has_more"] is False

    # record_access still stamps exactly the returned claim (not the probe row).
    verify = _call(client, "lore_recall", {"limit": 200, "offset": 0, "record_access": False})["structuredContent"]
    by_id = {claim["candidate_id"]: claim for claim in verify["claims"]}
    assert by_id[recalled_id]["access_count"] >= 1


def test_mcp_overview_is_complete_runtime_derived_index(client):
    from lore_app.mcp.tools import DEFAULT_TOOL_NAMES, TOOL_HANDLERS, TOOLS

    default_names = [tool["name"] for tool in rpc(client, "tools/list").json()["result"]["tools"]]
    assert default_names == list(DEFAULT_TOOL_NAMES)
    assert "lore_overview" not in default_names

    # lore_overview is a well-known, on-demand discovery call. It is
    # deliberately callable without adding a seventh schema to the default list.
    content = _call(client, "lore_overview", {})["structuredContent"]
    assert content["default_tools"] == list(DEFAULT_TOOL_NAMES)
    assert content["tools"] == TOOLS
    assert content["tool_count"] == len(TOOLS)
    assert {tool["name"] for tool in content["tools"]} == set(TOOL_HANDLERS)


def test_every_advanced_tool_remains_callable_on_demand(monkeypatch):
    from lore_app.mcp.tools import DEFAULT_TOOL_NAMES, TOOL_HANDLERS, TOOLS, call_tool

    registered = {tool["name"] for tool in TOOLS}
    assert registered == set(TOOL_HANDLERS)

    advanced = registered - set(DEFAULT_TOOL_NAMES)
    called: set[str] = set()

    def fake_handler(tool_name):
        def handle(_ctx):
            called.add(tool_name)
            return {"content": [], "structuredContent": {"name": tool_name}, "isError": False}

        return handle

    for name in advanced:
        monkeypatch.setitem(TOOL_HANDLERS, name, fake_handler(name))
        result = call_tool(object(), {"name": name, "arguments": {}})
        assert result["structuredContent"]["name"] == name

    assert called == advanced


def test_mcp_read_tools_have_readonly_annotations():
    from lore_app.mcp.tools import TOOLS

    by_name = {tool["name"]: tool for tool in TOOLS}
    for name in ("lore_list_traces", "lore_recall", "lore_overview"):
        annotations = by_name[name].get("annotations") or {}
        assert annotations.get("readOnlyHint") is True
        assert annotations.get("destructiveHint") is False


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
