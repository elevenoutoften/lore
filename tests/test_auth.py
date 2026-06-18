from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from lore_app.api_keys import LoreApiKeyStore
from lore_app.config import LoreConfig
from lore_app.main import create_app
from lore_app.route_utils import actor_from_request, client_rate_limit_key


def make_config(content_dir, search_db, tmp_path, mode: str, secret: str) -> LoreConfig:
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = search_db
    config.vector_db = tmp_path / "vectors.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.auth_mode = mode
    config.auth_secret = secret
    return config


def make_request(app, *, headers=None, client_host: str = "127.0.0.1") -> Request:
    raw_headers = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/pages",
            "headers": raw_headers,
            "client": (client_host, 12345),
            "app": app,
            "state": {},
        }
    )


def test_bearer_auth_protects_api(content_dir, search_db, tmp_path):
    app = create_app(make_config(content_dir, search_db, tmp_path, "bearer", "secret-token"))

    with TestClient(app) as client:
        unauthorized = client.get("/api/pages")
        authorized = client.get("/api/pages", headers={"Authorization": "Bearer secret-token"})
        health = client.get("/healthz")

    assert unauthorized.status_code == 401
    assert unauthorized.headers["WWW-Authenticate"] == "Bearer"
    assert authorized.status_code == 200
    assert health.status_code == 200


def test_bearer_rejects_empty_secret(content_dir, search_db, tmp_path):
    """Bearer auth must fail to start with an empty secret."""
    config = make_config(content_dir, search_db, tmp_path, "bearer", "")
    with pytest.raises(ValueError, match="non-empty"):
        create_app(config)


def test_bearer_rejects_whitespace_secret(content_dir, search_db, tmp_path):
    """Bearer auth must fail to start with a whitespace-only secret."""
    config = make_config(content_dir, search_db, tmp_path, "bearer", "   ")
    with pytest.raises(ValueError, match="non-empty"):
        create_app(config)


def test_bearer_accepts_valid_secret(content_dir, search_db, tmp_path):
    """Bearer auth with a valid secret must still work."""
    config = make_config(content_dir, search_db, tmp_path, "bearer", "valid-secret")
    app = create_app(config)

    with TestClient(app) as client:
        unauthorized = client.get("/api/pages")
        authorized = client.get("/api/pages", headers={"Authorization": "Bearer valid-secret"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_invalid_auth_mode_rejected(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "bearerr", "secret")
    with pytest.raises(ValueError, match="Unsupported"):
        create_app(config)


def test_invalid_auth_mode_rejected_no_secret(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "apikey", "")
    with pytest.raises(ValueError, match="Unsupported"):
        create_app(config)


def test_basic_auth_protects_api(content_dir, search_db, tmp_path):
    app = create_app(make_config(content_dir, search_db, tmp_path, "basic", "user:pass"))
    header = base64.b64encode(b"user:pass").decode("ascii")

    with TestClient(app) as client:
        unauthorized = client.get("/api/pages")
        authorized = client.get("/api/pages", headers={"Authorization": f"Basic {header}"})

    assert unauthorized.status_code == 401
    assert unauthorized.headers["WWW-Authenticate"] == "Basic"
    assert authorized.status_code == 200


def test_basic_rejects_empty_secret(content_dir, search_db, tmp_path):
    """Basic auth must fail to start with an empty secret."""
    config = make_config(content_dir, search_db, tmp_path, "basic", "")
    with pytest.raises(ValueError, match="non-empty"):
        create_app(config)


def test_basic_rejects_whitespace_secret(content_dir, search_db, tmp_path):
    """Basic auth must fail to start with a whitespace-only secret."""
    config = make_config(content_dir, search_db, tmp_path, "basic", "   ")
    with pytest.raises(ValueError, match="non-empty"):
        create_app(config)


def test_basic_accepts_valid_secret(content_dir, search_db, tmp_path):
    """Basic auth with a valid secret must still work."""
    config = make_config(content_dir, search_db, tmp_path, "basic", "user:pass")
    header = base64.b64encode(b"user:pass").decode("ascii")
    app = create_app(config)

    with TestClient(app) as client:
        unauthorized = client.get("/api/pages")
        authorized = client.get("/api/pages", headers={"Authorization": f"Basic {header}"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_none_auth_works_without_secret(content_dir, search_db, tmp_path):
    """None auth must work without any secret."""
    config = make_config(content_dir, search_db, tmp_path, "none", "")
    app = create_app(config)

    with TestClient(app) as client:
        response = client.get("/api/pages")

    assert response.status_code == 200


def test_actor_from_request_ignores_x_lore_actor_without_trusted_headers(content_dir, search_db, tmp_path):
    """X-Lore-Actor header is ignored when trusted_headers is False."""
    config = make_config(content_dir, search_db, tmp_path, "none", "")
    config.trusted_headers = False
    app = create_app(config)
    request = make_request(app, headers={"X-Lore-Actor": "spoofed"})

    assert actor_from_request(request) == "anonymous"


def test_actor_from_request_uses_x_lore_actor_with_trusted_headers(content_dir, search_db, tmp_path):
    """X-Lore-Actor header is used when trusted_headers is True."""
    config = make_config(content_dir, search_db, tmp_path, "none", "")
    config.trusted_headers = True
    app = create_app(config)
    request = make_request(app, headers={"X-Lore-Actor": "proxy-injected"})

    assert actor_from_request(request) == "proxy-injected"


def test_actor_from_request_uses_auth_actor_over_anonymous(content_dir, search_db, tmp_path):
    """Authenticated actor from auth middleware is used regardless of trusted_headers."""
    config = make_config(content_dir, search_db, tmp_path, "bearer", "test-secret")
    config.trusted_headers = False
    app = create_app(config)
    request = make_request(app, headers={"X-Lore-Actor": "spoofed"})
    request.state.lore_actor = "bearer"

    assert actor_from_request(request) == "bearer"


def test_actor_from_request_ignores_header_when_authenticated(content_dir, search_db, tmp_path):
    """X-Lore-Actor is ignored for authenticated requests even with trusted_headers."""
    config = make_config(content_dir, search_db, tmp_path, "bearer", "test-secret")
    config.trusted_headers = True
    app = create_app(config)
    request = make_request(app, headers={"X-Lore-Actor": "attacker-spoof"})
    request.state.lore_actor = "bearer"

    assert actor_from_request(request) == "bearer"


def test_rate_limit_key_ignores_forwarded_for_without_trusted_headers(content_dir, search_db, tmp_path):
    """Rate limit key uses direct client IP when trusted_headers is False."""
    config = make_config(content_dir, search_db, tmp_path, "none", "")
    config.trusted_headers = False
    app = create_app(config)
    request = make_request(app, headers={"X-Forwarded-For": "10.0.0.1"}, client_host="127.0.0.1")

    assert client_rate_limit_key(request) == "127.0.0.1"


def test_rate_limit_key_uses_forwarded_for_with_trusted_headers(content_dir, search_db, tmp_path):
    """Rate limit key uses X-Forwarded-For when trusted_headers is True."""
    config = make_config(content_dir, search_db, tmp_path, "none", "")
    config.trusted_headers = True
    app = create_app(config)
    request = make_request(app, headers={"X-Forwarded-For": "10.0.0.1"}, client_host="127.0.0.1")

    assert client_rate_limit_key(request) == "10.0.0.1"


def test_lore_api_key_auth_protects_api(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _api_key_obj, api_key = store.create_key(name="codex", role="writer")
    app = create_app(config)

    with TestClient(app) as client:
        unauthorized = client.get("/api/pages")
        flow_key = client.get("/api/pages", headers={"Authorization": "Bearer flow_not_a_lore_key"})
        authorized = client.get("/api/pages", headers={"Authorization": f"Bearer {api_key}"})

    assert unauthorized.status_code == 401
    assert flow_key.status_code == 401
    assert authorized.status_code == 200


def test_api_key_auth_works_without_secret(content_dir, search_db, tmp_path):
    """API key auth must work without a bearer/basic secret."""
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    app = create_app(config)

    with TestClient(app) as client:
        response = client.get("/api/pages")

    assert response.status_code == 401


def test_trusted_proxy_auth_is_opt_in(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_proxy_auth = False
    app = create_app(config)

    with TestClient(app) as client:
        response = client.get("/api/pages", headers={"X-Axis-User": "alice"})

    assert response.status_code == 401


def test_trusted_proxy_auth_allows_reader_after_api_key_auth_fails(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_proxy_auth = True
    app = create_app(config)

    with TestClient(app) as client:
        headers = {
            "Authorization": "Bearer flow_not_a_lore_key",
            "X-Axis-User": "alice",
        }
        read = client.get("/api/pages", headers=headers)
        write = client.put(
            "/api/pages/services/proxy-reader-write",
            json={"content": "# Proxy Reader Write"},
            headers=headers,
        )

    assert read.status_code == 200
    assert write.status_code == 403


def test_trusted_proxy_admin_can_manage_api_keys(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_proxy_auth = True
    app = create_app(config)

    with TestClient(app) as client:
        headers = {"X-Axis-User": "admin@example.com", "X-Axis-Admin": "1"}
        listed = client.get("/api/api-keys", headers=headers)
        created = client.post("/api/api-keys", json={"name": "proxy-created"}, headers=headers)

    assert listed.status_code == 200
    assert created.status_code == 201
    assert created.json()["name"] == "proxy-created"


def test_trusted_proxy_auth_falls_back_for_bearer_mode(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "bearer", "valid-secret")
    config.trusted_proxy_auth = True
    app = create_app(config)

    with TestClient(app) as client:
        response = client.get("/api/pages", headers={"X-Lore-Agent": "nyx"})

    assert response.status_code == 200


def test_healthz_config_is_public(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_headers = True
    config.trusted_proxy_auth = True
    app = create_app(config)

    with TestClient(app) as client:
        response = client.get("/healthz/config")

    assert response.status_code == 200
    assert response.json() == {
        "auth_mode": "api_key",
        "trusted_headers": True,
        "trusted_proxy_auth": True,
    }


def test_revoked_lore_api_key_is_rejected(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _admin_key_obj, admin_key = store.create_key(name="admin-key", role="admin")
    app = create_app(config)

    with TestClient(app) as client:
        admin_headers = {"Authorization": f"Bearer {admin_key}"}
        created = client.post("/api/api-keys", json={"name": "temporary"}, headers=admin_headers).json()
        assert client.get("/api/pages", headers={"Authorization": f"Bearer {created['api_key']}"}).status_code == 200

        revoked = client.post(f"/api/api-keys/{created['id']}/revoke", headers=admin_headers)
        rejected = client.get("/api/pages", headers={"Authorization": f"Bearer {created['api_key']}"})

    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert rejected.status_code == 401


def test_public_path_prefix_does_not_leak_pages(content_dir, search_db, tmp_path):
    """Page ids that merely start with 'static'/'healthz' must not bypass auth.

    Regression for the prefix-match auth bypass: a substring prefix check on the
    public-path whitelist let GET /staticsecret and GET /healthznotes (served by
    the catch-all reader route) skip authentication entirely.
    """
    (content_dir / "staticsecret.md").write_text(
        "---\ntitle: Static Secret\nkind: note\nvisibility: internal\n---\n\n# top secret\n",
        encoding="utf-8",
    )
    (content_dir / "healthznotes.md").write_text(
        "---\ntitle: Healthz Notes\nkind: note\nvisibility: internal\n---\n\n# private\n",
        encoding="utf-8",
    )
    app = create_app(make_config(content_dir, search_db, tmp_path, "bearer", "secret-token"))

    with TestClient(app) as client:
        leaked_static = client.get("/staticsecret")
        leaked_healthz = client.get("/healthznotes")
        real_static = client.get("/static/lore.css")
        health = client.get("/healthz")
        authorized = client.get("/staticsecret", headers={"Authorization": "Bearer secret-token"})

    assert leaked_static.status_code == 401
    assert "top secret" not in leaked_static.text
    assert leaked_healthz.status_code == 401
    assert real_static.status_code == 200
    assert health.status_code == 200
    assert authorized.status_code == 200


def test_reader_lore_api_key_cannot_write(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _admin_key_obj, admin_key = store.create_key(name="admin-key", role="admin")
    app = create_app(config)

    with TestClient(app) as client:
        admin_headers = {"Authorization": f"Bearer {admin_key}"}
        created = client.post(
            "/api/api-keys",
            json={"name": "reader", "role": "reader"},
            headers=admin_headers,
        ).json()
        headers = {"Authorization": f"Bearer {created['api_key']}"}

        read = client.get("/api/pages", headers=headers)
        write = client.put(
            "/api/pages/services/reader-write",
            json={"content": "# Reader Write"},
            headers=headers,
        )
        mcp_write_surface = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "lore_upsert_page", "arguments": {}},
            },
            headers=headers,
        )

    assert read.status_code == 200
    assert write.status_code == 403
    assert mcp_write_surface.status_code == 403


def test_reader_mcp_can_read_but_not_write(content_dir, search_db, tmp_path):
    """A reader-role token can initialize, list tools, and call read tools over MCP,
    but is 403'd on write tools."""
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _admin_key_obj, admin_key = store.create_key(name="admin-key", role="admin")
    app = create_app(config)

    with TestClient(app) as client:
        admin_headers = {"Authorization": f"Bearer {admin_key}"}
        created = client.post(
            "/api/api-keys",
            json={"name": "reader", "role": "reader"},
            headers=admin_headers,
        ).json()
        headers = {"Authorization": f"Bearer {created['api_key']}"}

        # Reader can initialize
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
            headers=headers,
        )
        # Reader can list tools
        tool_list = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
            headers=headers,
        )
        # Reader can call a read tool (lore_search)
        search = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "lore_search", "arguments": {"query": "test"}},
            },
            headers=headers,
        )
        # Reader is blocked from a write tool (lore_capture)
        write = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "lore_capture", "arguments": {"text": "test"}},
            },
            headers=headers,
        )

    assert init.status_code == 200
    assert tool_list.status_code == 200
    assert search.status_code == 200
    assert write.status_code == 403
