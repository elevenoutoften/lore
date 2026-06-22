from __future__ import annotations

from fastapi.testclient import TestClient

from lore_app.config import LoreConfig
from lore_app.main import create_app


def _app(content_dir, search_db, tmp_path, *, auto_consolidate: bool = False):
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = search_db
    config.vector_db = tmp_path / "vectors.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.settings_db = tmp_path / "settings.db"
    config.auth_mode = "api_key"
    config.auto_consolidate = auto_consolidate
    return create_app(config)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mcp_call(client: TestClient, headers: dict[str, str], name: str, arguments: dict) -> dict:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_capture_surfaces_server_stamp_authenticated_actor(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path)
    _, raw_key = app.state.api_key_store.create_key(name="agent-a", role="writer")
    headers = _headers(raw_key)

    with TestClient(app) as client:
        rest_capture = client.post(
            "/api/capture",
            json={
                "title": "REST spoof attempt",
                "observation": "Payload actor must not win on REST capture.",
                "namespace": "notes",
                "agent": "payload-agent",
                "actor": "payload-actor",
                "provenance": {"actor": "nested-payload-actor"},
                "capture_date": "2026-06-18",
            },
            headers=headers,
        )
        memory_capture = client.post(
            "/api/memory/capture",
            json={
                "text": "Payload actor must not win on memory capture.",
                "namespace": "notes",
                "agent_name": "payload-agent",
                "actor": "payload-actor",
                "provenance": {"actor": "nested-payload-actor"},
                "metadata": {"title": "Memory spoof attempt", "capture_date": "2026-06-18"},
            },
            headers=headers,
        )
        mcp_capture = _mcp_call(
            client,
            headers,
            "lore_capture",
            {
                "title": "MCP spoof attempt",
                "observation": "Payload actor must not win on MCP capture.",
                "namespace": "notes",
                "agent": "payload-agent",
                "actor": "payload-actor",
                "provenance": {"actor": "nested-payload-actor"},
                "capture_date": "2026-06-18",
            },
        )

    assert rest_capture.status_code == 201, rest_capture.text
    rest_page = rest_capture.json()["page"]
    assert rest_page["id"] == "notes/agent-a/2026-06-18/rest-spoof-attempt"
    assert rest_page["frontmatter"]["actor"] == "agent-a"
    assert rest_page["frontmatter"]["provenance"]["actor"] == "agent-a"

    assert memory_capture.status_code == 201, memory_capture.text
    memory_page = client.app.state.repository.read_page(memory_capture.json()["capture_id"])
    assert memory_page is not None
    assert memory_page.id == "notes/agent-a/2026-06-18/memory-spoof-attempt"
    assert memory_page.frontmatter["actor"] == "agent-a"
    assert memory_page.frontmatter["provenance"]["actor"] == "agent-a"

    mcp_page = mcp_capture["result"]["structuredContent"]["page"]
    assert mcp_page["id"] == "notes/agent-a/2026-06-18/mcp-spoof-attempt"
    assert mcp_page["frontmatter"]["actor"] == "agent-a"
    assert mcp_page["frontmatter"]["provenance"]["actor"] == "agent-a"


def test_rest_recall_is_scoped_to_authenticated_actor(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path, auto_consolidate=True)
    _, key_a = app.state.api_key_store.create_key(name="agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="agent-b", role="writer")
    _, admin_key = app.state.api_key_store.create_key(name="agent-admin", role="admin")
    unique_term = "TenantRecallNeedleRest"

    with TestClient(app) as client:
        captured = client.post(
            "/api/memory/capture",
            json={
                "text": f"{unique_term} belongs only to agent B.",
                "actor": "agent-a",
                "agent_name": "agent-a",
                "metadata": {"title": "Tenant recall B", "confidence": "high"},
            },
            headers=_headers(key_b),
        )
        assert captured.status_code == 201, captured.text

        b_recall = client.get(
            "/api/memory/recall",
            params={"query": unique_term, "limit": 5},
            headers=_headers(key_b),
        )
        a_recall = client.get(
            "/api/memory/recall",
            params={"query": unique_term, "limit": 5},
            headers=_headers(key_a),
        )
        a_spoof = client.get(
            "/api/memory/recall",
            params={"query": unique_term, "actor": "agent-b", "limit": 5},
            headers=_headers(key_a),
        )
        admin_default = client.get(
            "/api/memory/recall",
            params={"query": unique_term, "limit": 5},
            headers=_headers(admin_key),
        )
        admin_cross = client.get(
            "/api/memory/recall",
            params={"query": unique_term, "actor": "agent-b", "cross_actor": "true", "limit": 5},
            headers=_headers(admin_key),
        )

    assert b_recall.status_code == 200, b_recall.text
    assert any(claim["actor"] == "agent-b" for claim in b_recall.json()["claims"])

    assert a_recall.status_code == 200, a_recall.text
    assert a_recall.json()["count"] == 0

    assert a_spoof.status_code == 403
    assert "cross-actor" in a_spoof.json()["detail"].lower()

    assert admin_default.status_code == 200, admin_default.text
    assert admin_default.json()["count"] == 0

    assert admin_cross.status_code == 200, admin_cross.text
    assert any(claim["actor"] == "agent-b" for claim in admin_cross.json()["claims"])


def test_rag_vector_hits_are_scoped_to_authenticated_actor(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path, auto_consolidate=False)
    _, key_a = app.state.api_key_store.create_key(name="rag-agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="rag-agent-b", role="writer")
    unique_term = "TenantVectorNeedleRag"

    with TestClient(app) as client:
        captured = client.post(
            "/api/memory/capture",
            json={
                "text": f"{unique_term} belongs only to RAG agent B.",
                "lane": "ops",
                "metadata": {"title": "Tenant vector B", "confidence": "high"},
            },
            headers=_headers(key_b),
        )
        assert captured.status_code == 201, captured.text
        page_id = captured.json()["capture_id"]

        b_rag = client.post(
            "/api/rag/retrieve",
            json={"query": unique_term, "limit": 5, "expand_hops": 0, "lane": "ops"},
            headers=_headers(key_b),
        )
        a_rag = client.post(
            "/api/rag/retrieve",
            json={"query": unique_term, "limit": 5, "expand_hops": 0, "lane": "ops"},
            headers=_headers(key_a),
        )

    assert b_rag.status_code == 200, b_rag.text
    assert any(row["page_id"] == page_id for row in b_rag.json()["results"])
    assert any("vector" in row.get("sources", []) for row in b_rag.json()["results"])

    assert a_rag.status_code == 200, a_rag.text
    assert all(row["page_id"] != page_id for row in a_rag.json()["results"])
    assert a_rag.json()["results"] == []


def test_mcp_recall_is_scoped_to_authenticated_actor(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path, auto_consolidate=True)
    _, key_a = app.state.api_key_store.create_key(name="mcp-agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="mcp-agent-b", role="writer")
    _, admin_key = app.state.api_key_store.create_key(name="mcp-admin", role="admin")
    unique_term = "TenantRecallNeedleMcp"

    with TestClient(app) as client:
        captured = _mcp_call(
            client,
            _headers(key_b),
            "lore_capture",
            {
                "title": "MCP tenant recall B",
                "observation": f"{unique_term} belongs only to MCP agent B.",
                "actor": "mcp-agent-a",
                "agent": "mcp-agent-a",
            },
        )
        assert captured["result"]["isError"] is False
        assert captured["result"]["structuredContent"]["page"]["frontmatter"]["actor"] == "mcp-agent-b"

        b_recall = _mcp_call(client, _headers(key_b), "lore_recall", {"query": unique_term, "limit": 5})
        a_recall = _mcp_call(client, _headers(key_a), "lore_recall", {"query": unique_term, "limit": 5})
        a_spoof = _mcp_call(
            client,
            _headers(key_a),
            "lore_recall",
            {"query": unique_term, "actor": "mcp-agent-b", "limit": 5},
        )
        admin_default = _mcp_call(client, _headers(admin_key), "lore_recall", {"query": unique_term, "limit": 5})
        admin_cross = _mcp_call(
            client,
            _headers(admin_key),
            "lore_recall",
            {"query": unique_term, "actor": "mcp-agent-b", "cross_actor": True, "limit": 5},
        )

    b_payload = b_recall["result"]["structuredContent"]
    assert any(claim["actor"] == "mcp-agent-b" for claim in b_payload["claims"])

    assert a_recall["result"]["structuredContent"]["count"] == 0
    assert "error" in a_spoof
    assert "cross-actor" in a_spoof["error"]["message"].lower()

    assert admin_default["result"]["structuredContent"]["count"] == 0
    assert any(claim["actor"] == "mcp-agent-b" for claim in admin_cross["result"]["structuredContent"]["claims"])


def test_all_claim_read_and_ack_surfaces_enforce_actor_scope(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path, auto_consolidate=True)
    _, key_a = app.state.api_key_store.create_key(name="surface-agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="surface-agent-b", role="writer")
    _, admin_key = app.state.api_key_store.create_key(name="surface-admin", role="admin")
    unique_term = "TenantSurfaceNeedle"

    with TestClient(app) as client:
        captured = client.post(
            "/api/memory/capture",
            json={
                "text": f"{unique_term} belongs only to surface agent B.",
                "metadata": {
                    "title": "Tenant surface B",
                    "confidence": "high",
                    "epistemic_status": "assumption",
                },
            },
            headers=_headers(key_b),
        )
        assert captured.status_code == 201, captured.text

        b_recall = client.get(
            "/api/memory/recall",
            params={"query": unique_term, "limit": 5},
            headers=_headers(key_b),
        )
        candidate_id = b_recall.json()["claims"][0]["candidate_id"]

        a_claims = client.get("/api/ledger/claims", headers=_headers(key_a))
        b_claims = client.get("/api/ledger/claims", headers=_headers(key_b))
        admin_claims = client.get(
            "/api/ledger/claims",
            params={"actor": "surface-agent-b", "cross_actor": "true"},
            headers=_headers(admin_key),
        )
        a_candidates = client.get("/api/ledger/candidates", headers=_headers(key_a))
        a_extraction = client.get("/api/extraction/candidates", headers=_headers(key_a))
        a_rag = _mcp_call(client, _headers(key_a), "lore_rag_context", {"query": unique_term, "limit": 5})
        a_actors = _mcp_call(client, _headers(key_a), "lore_list_actors", {})
        a_graph = client.get("/api/context-graph", headers=_headers(key_a))
        a_graph_analytics = client.get("/api/graph/analytics", headers=_headers(key_a))
        admin_graph_analytics = client.get(
            "/api/graph/analytics",
            params={"actor": "surface-agent-b", "cross_actor": "true"},
            headers=_headers(admin_key),
        )
        a_blocked = client.get("/api/consolidation/blocked", headers=_headers(key_a))
        admin_blocked = client.get(
            "/api/consolidation/blocked",
            params={"actor": "surface-agent-b", "cross_actor": "true"},
            headers=_headers(admin_key),
        )

        a_ack = client.post(
            "/api/memory/recall/ack",
            json={"candidate_ids": [candidate_id]},
            headers=_headers(key_a),
        )
        a_mcp_ack = _mcp_call(
            client,
            _headers(key_a),
            "lore_ack_recall",
            {"candidate_ids": [candidate_id]},
        )
        b_ack = client.post(
            "/api/memory/recall/ack",
            json={"candidate_ids": [candidate_id]},
            headers=_headers(key_b),
        )

    assert a_claims.status_code == 200
    assert a_claims.json()["count"] == 0
    assert any(claim["candidate_id"] == candidate_id for claim in b_claims.json()["claims"])
    assert any(claim["candidate_id"] == candidate_id for claim in admin_claims.json()["claims"])
    assert all(row["candidate_id"] != candidate_id for row in a_candidates.json())
    assert all(row["candidate_id"] != candidate_id for row in a_extraction.json()["candidates"])
    assert candidate_id not in str(a_rag["result"]["structuredContent"])
    assert all(row["actor"] != "surface-agent-b" for row in a_actors["result"]["structuredContent"]["actors"])
    assert candidate_id not in a_graph.text
    assert a_graph_analytics.status_code == 200, a_graph_analytics.text
    assert admin_graph_analytics.status_code == 200, admin_graph_analytics.text
    assert candidate_id not in a_graph_analytics.text
    assert candidate_id in admin_graph_analytics.text
    assert a_blocked.status_code == 200, a_blocked.text
    assert admin_blocked.status_code == 200, admin_blocked.text
    assert all(row["claim_id"] != candidate_id for row in a_blocked.json()["blocked"])
    assert any(row["claim_id"] == candidate_id for row in admin_blocked.json()["blocked"])

    assert a_ack.status_code == 200
    assert a_ack.json()["acknowledged_count"] == 0
    assert a_mcp_ack["result"]["structuredContent"]["acknowledged_count"] == 0
    assert b_ack.status_code == 200
    assert b_ack.json()["acknowledged_count"] == 1


def test_trace_and_precedent_reads_are_scoped_to_authenticated_actor(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path)
    _, key_a = app.state.api_key_store.create_key(name="trace-agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="trace-agent-b", role="writer")
    _, admin_key = app.state.api_key_store.create_key(name="trace-admin", role="admin")
    unique_term = "TenantTraceNeedleB"

    with TestClient(app) as client:
        created = client.post(
            "/api/traces",
            json={
                "actor": "trace-agent-b",
                "reason_summary": f"{unique_term} belongs only to trace agent B.",
                "status": "completed",
                "related_ids": {"task_id": "flow_000843"},
                "provenance": {"task_ids": ["flow_000843"], "actor": "trace-agent-b"},
            },
            headers=_headers(key_b),
        )
        assert created.status_code == 201, created.text
        trace = created.json()
        trace_id = trace["trace_id"]

        a_list = client.get("/api/traces", headers=_headers(key_a))
        b_list = client.get("/api/traces", headers=_headers(key_b))
        admin_list = client.get(
            "/api/traces",
            params={"actor": "trace-agent-b", "cross_actor": "true"},
            headers=_headers(admin_key),
        )
        a_get = client.get(f"/api/traces/{trace_id}", headers=_headers(key_a))
        a_provenance = client.get(f"/api/provenance/trace/{trace_id}", headers=_headers(key_a))

        a_mcp_list = _mcp_call(client, _headers(key_a), "lore_list_traces", {"limit": 10})
        a_mcp_get = _mcp_call(client, _headers(key_a), "lore_get_trace", {"trace_id": trace_id})
        a_mcp_provenance = _mcp_call(
            client,
            _headers(key_a),
            "lore_get_provenance",
            {"entity_type": "trace", "entity_id": trace_id},
        )
        a_mcp_precedents = _mcp_call(
            client, _headers(key_a), "lore_find_precedents", {"keyword": unique_term, "limit": 10}
        )
        b_mcp_precedents = _mcp_call(
            client, _headers(key_b), "lore_find_precedents", {"keyword": unique_term, "limit": 10}
        )
        admin_mcp_list = _mcp_call(
            client,
            _headers(admin_key),
            "lore_list_traces",
            {"actor": "trace-agent-b", "cross_actor": True, "limit": 10},
        )

    assert a_list.status_code == 200, a_list.text
    assert all(item["trace_id"] != trace_id for item in a_list.json()["traces"])
    assert a_list.json()["total"] == 0

    assert b_list.status_code == 200, b_list.text
    assert any(item["trace_id"] == trace_id for item in b_list.json()["traces"])

    assert admin_list.status_code == 200, admin_list.text
    assert any(item["trace_id"] == trace_id for item in admin_list.json()["traces"])

    assert a_get.status_code == 403
    assert a_provenance.status_code == 403

    assert a_mcp_list["result"]["structuredContent"]["total"] == 0
    assert all(item["trace_id"] != trace_id for item in a_mcp_list["result"]["structuredContent"]["traces"])
    assert "error" in a_mcp_get
    assert "cross-actor" in a_mcp_get["error"]["message"].lower()
    assert "error" in a_mcp_provenance
    assert "cross-actor" in a_mcp_provenance["error"]["message"].lower()

    assert a_mcp_precedents["result"]["structuredContent"]["total"] == 0
    assert unique_term not in str(a_mcp_precedents["result"]["structuredContent"])

    b_precedent_matches = b_mcp_precedents["result"]["structuredContent"]["matches"]
    assert any(match["id"] == f"trace:{trace_id}" for match in b_precedent_matches)

    assert any(item["trace_id"] == trace_id for item in admin_mcp_list["result"]["structuredContent"]["traces"])


def test_trace_update_is_scoped_to_authenticated_actor(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path)
    _, key_a = app.state.api_key_store.create_key(name="trace-agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="trace-agent-b", role="writer")
    _, admin_key = app.state.api_key_store.create_key(name="trace-admin", role="admin")

    with TestClient(app) as client:
        created = client.post(
            "/api/traces",
            json={
                "actor": "trace-agent-b",
                "reason_summary": "Trace owned by agent B.",
                "status": "active",
                "related_ids": {"task_id": "flow_000875"},
                "provenance": {"task_ids": ["flow_000875"], "actor": "trace-agent-b"},
            },
            headers=_headers(key_b),
        )
        assert created.status_code == 201, created.text
        trace_id = created.json()["trace_id"]

        a_patch = client.patch(
            f"/api/traces/{trace_id}",
            json={"status": "completed"},
            headers=_headers(key_a),
        )
        b_patch = client.patch(
            f"/api/traces/{trace_id}",
            json={"status": "completed"},
            headers=_headers(key_b),
        )
        admin_patch = client.patch(
            f"/api/traces/{trace_id}",
            params={"cross_actor": "true"},
            json={"status": "abandoned"},
            headers=_headers(admin_key),
        )

    assert a_patch.status_code == 403, a_patch.text

    assert b_patch.status_code == 200, b_patch.text
    assert b_patch.json()["status"] == "completed"

    assert admin_patch.status_code == 200, admin_patch.text
    assert admin_patch.json()["status"] == "abandoned"


def test_trace_create_binds_actor_to_authenticated_caller(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path)
    _, key_a = app.state.api_key_store.create_key(name="trace-agent-a", role="writer")
    app.state.api_key_store.create_key(name="trace-agent-b", role="writer")
    _, admin_key = app.state.api_key_store.create_key(name="trace-admin", role="admin")

    with TestClient(app) as client:
        # REST: a writer key cannot attribute a trace to another actor.
        rest = client.post(
            "/api/traces",
            json={"actor": "trace-agent-b", "reason_summary": "Spoof attempt.", "status": "active"},
            headers=_headers(key_a),
        )
        assert rest.status_code == 201, rest.text
        assert rest.json()["actor"] == "trace-agent-a"

        # MCP: same spoof attempt is bound to the caller.
        mcp = _mcp_call(
            client,
            _headers(key_a),
            "lore_create_trace",
            {"actor": "trace-agent-b", "reason_summary": "MCP spoof.", "status": "active"},
        )
        assert mcp["result"]["isError"] is False
        assert mcp["result"]["structuredContent"]["actor"] == "trace-agent-a"

        # An admin key may still set an explicit cross-actor actor.
        admin_rest = client.post(
            "/api/traces",
            json={"actor": "trace-agent-b", "reason_summary": "Admin explicit.", "status": "active"},
            headers=_headers(admin_key),
        )
        assert admin_rest.status_code == 201, admin_rest.text
        assert admin_rest.json()["actor"] == "trace-agent-b"


def test_rag_include_traces_does_not_leak_cross_actor_trace_ids(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path)
    _, key_a = app.state.api_key_store.create_key(name="rag-trace-agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="rag-trace-agent-b", role="writer")
    unique_term = "TenantRagTraceNeedle"

    with TestClient(app) as client:
        captured = client.post(
            "/api/memory/capture",
            json={
                "text": f"{unique_term} belongs only to RAG trace agent A.",
                "metadata": {"title": "Tenant rag trace A", "confidence": "high"},
            },
            headers=_headers(key_a),
        )
        assert captured.status_code == 201, captured.text
        page_id = captured.json()["capture_id"]

        own_trace = client.post(
            "/api/traces",
            json={
                "actor": "rag-trace-agent-a",
                "reason_summary": "Agent A's own reasoning over its own page.",
                "status": "completed",
                "provenance": {"page_ids": [page_id], "actor": "rag-trace-agent-a"},
            },
            headers=_headers(key_a),
        )
        assert own_trace.status_code == 201, own_trace.text
        own_trace_id = own_trace.json()["trace_id"]

        secret_trace = client.post(
            "/api/traces",
            json={
                "actor": "rag-trace-agent-b",
                "reason_summary": "Secret cross-actor reasoning by agent B over agent A's page.",
                "status": "completed",
                "provenance": {"page_ids": [page_id], "actor": "rag-trace-agent-b"},
            },
            headers=_headers(key_b),
        )
        assert secret_trace.status_code == 201, secret_trace.text
        secret_trace_id = secret_trace.json()["trace_id"]

        a_rag = client.post(
            "/api/rag/retrieve",
            json={"query": unique_term, "limit": 5, "expand_hops": 1, "include_traces": True},
            headers=_headers(key_a),
        )

    assert a_rag.status_code == 200, a_rag.text
    a_rows = {row["page_id"]: row for row in a_rag.json()["results"]}
    assert page_id in a_rows, "expected agent A's own page in its own retrieval results"
    related = a_rows[page_id].get("related_traces", [])
    # Positive control: A's own trace is correctly surfaced, proving the enrichment path runs.
    assert own_trace_id in related
    # Leak 1 regression: agent B's trace id must not surface in agent A's related_traces.
    assert secret_trace_id not in related
    assert secret_trace_id not in a_rag.text


def test_precedent_graph_does_not_leak_cross_actor_trace_nodes(content_dir, search_db, tmp_path):
    app = _app(content_dir, search_db, tmp_path)
    _, key_a = app.state.api_key_store.create_key(name="prec-graph-agent-a", role="writer")
    _, key_b = app.state.api_key_store.create_key(name="prec-graph-agent-b", role="writer")
    task_ref = "flow_000874b"

    with TestClient(app) as client:
        created = client.post(
            "/api/traces",
            json={
                "actor": "prec-graph-agent-b",
                "reason_summary": "Secret cross-actor reasoning by agent B for a shared task.",
                "status": "completed",
                "related_ids": {"task_id": task_ref},
                "provenance": {"task_ids": [task_ref], "actor": "prec-graph-agent-b"},
            },
            headers=_headers(key_b),
        )
        assert created.status_code == 201, created.text
        secret_trace_id = created.json()["trace_id"]

        a_precedents = client.post(
            "/api/precedents",
            json={"task_ref": task_ref, "limit": 20},
            headers=_headers(key_a),
        )
        b_precedents = client.post(
            "/api/precedents",
            json={"task_ref": task_ref, "limit": 20},
            headers=_headers(key_b),
        )

    assert b_precedents.status_code == 200, b_precedents.text
    # Positive control: the owner reaches its own trace via the same task-graph path.
    assert any(match["id"] == f"trace:{secret_trace_id}" for match in b_precedents.json()["matches"])

    assert a_precedents.status_code == 200, a_precedents.text
    a_matches = a_precedents.json()["matches"]
    # Leak 2 regression: agent B's trace node must not surface in agent A's precedent graph search.
    assert all(match["id"] != f"trace:{secret_trace_id}" for match in a_matches)
    assert secret_trace_id not in a_precedents.text
