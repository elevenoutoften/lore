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
