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
                "capture_date": "2026-06-18",
            },
        )

    assert rest_capture.status_code == 201, rest_capture.text
    rest_page = rest_capture.json()["page"]
    assert rest_page["id"] == "notes/agent-a/2026-06-18/rest-spoof-attempt"
    assert rest_page["frontmatter"]["actor"] == "agent-a"

    assert memory_capture.status_code == 201, memory_capture.text
    memory_page = client.app.state.repository.read_page(memory_capture.json()["capture_id"])
    assert memory_page is not None
    assert memory_page.id == "notes/agent-a/2026-06-18/memory-spoof-attempt"
    assert memory_page.frontmatter["actor"] == "agent-a"

    mcp_page = mcp_capture["result"]["structuredContent"]["page"]
    assert mcp_page["id"] == "notes/agent-a/2026-06-18/mcp-spoof-attempt"
    assert mcp_page["frontmatter"]["actor"] == "agent-a"


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
