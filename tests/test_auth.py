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


def _metrics_auth_headers(config: LoreConfig, mode: str) -> dict[str, str]:
    if mode == "bearer":
        return {"Authorization": f"Bearer {config.auth_secret}"}
    if mode == "basic":
        credential = base64.b64encode(config.auth_secret.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {credential}"}
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _, api_key = store.create_key(name="metrics-reader", role="reader")
    return {"Authorization": f"Bearer {api_key}"}


@pytest.mark.parametrize(
    ("mode", "secret"),
    [
        ("bearer", "secret-token"),
        ("basic", "user:pass"),
        ("api_key", ""),
    ],
)
def test_metrics_requires_auth_by_default(content_dir, search_db, tmp_path, mode, secret):
    config = make_config(content_dir, search_db, tmp_path, mode, secret)
    headers = _metrics_auth_headers(config, mode)
    app = create_app(config)

    with TestClient(app) as client:
        unauthorized = client.get("/metrics")
        authorized = client.get("/metrics", headers=headers)

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert "lore_recall_requests" in authorized.text


@pytest.mark.parametrize(
    ("mode", "secret"),
    [
        ("bearer", "secret-token"),
        ("basic", "user:pass"),
        ("api_key", ""),
    ],
)
def test_metrics_public_flag_allows_unauthenticated_metrics(content_dir, search_db, tmp_path, mode, secret):
    config = make_config(content_dir, search_db, tmp_path, mode, secret)
    config.metrics_public = True
    app = create_app(config)

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "lore_recall_requests" in response.text


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
    config.trusted_proxy_secret = "proxy-secret"
    app = create_app(config)

    with TestClient(app) as client:
        headers = {
            "Authorization": "Bearer flow_not_a_lore_key",
            "X-Axis-User": "alice",
            "X-Lore-Proxy-Secret": "proxy-secret",
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
    config.trusted_proxy_secret = "proxy-secret"
    app = create_app(config)

    with TestClient(app) as client:
        headers = {
            "X-Axis-User": "admin@example.com",
            "X-Axis-Admin": "1",
            "X-Lore-Proxy-Secret": "proxy-secret",
        }
        listed = client.get("/api/api-keys", headers=headers)
        created = client.post("/api/api-keys", json={"name": "proxy-created"}, headers=headers)

    assert listed.status_code == 200
    assert created.status_code == 201
    assert created.json()["name"] == "proxy-created"


def test_trusted_proxy_auth_falls_back_for_bearer_mode(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "bearer", "valid-secret")
    config.trusted_proxy_auth = True
    config.trusted_proxy_secret = "proxy-secret"
    app = create_app(config)

    with TestClient(app) as client:
        response = client.get(
            "/api/pages",
            headers={"X-Lore-Agent": "nyx", "X-Lore-Proxy-Secret": "proxy-secret"},
        )

    assert response.status_code == 200


def test_trusted_proxy_admin_not_promoted_from_unallowlisted_source(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_proxy_auth = True
    config.trusted_proxy_cidrs = ["10.0.0.0/8"]  # does NOT include the test source IP
    app = create_app(config)

    headers = {"X-Axis-User": "admin@example.com", "X-Axis-Admin": "1"}
    with TestClient(app, client=("203.0.113.7", 12345)) as client:
        response = client.get("/api/api-keys", headers=headers)

    # The X-Axis-Admin spoof from a non-allowlisted origin must NOT be promoted.
    assert response.status_code == 401


def test_trusted_proxy_admin_promoted_from_allowlisted_cidr(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_proxy_auth = True
    config.trusted_proxy_cidrs = ["10.0.0.0/8"]
    app = create_app(config)

    headers = {"X-Axis-User": "admin@example.com", "X-Axis-Admin": "1"}
    with TestClient(app, client=("10.0.0.5", 12345)) as client:
        response = client.get("/api/api-keys", headers=headers)

    assert response.status_code == 200


def test_trusted_proxy_auth_honors_private_origin_without_explicit_proof(content_dir, search_db, tmp_path):
    """Backward compat: TRUSTED_PROXY_AUTH=true with no secret/CIDRs must keep an
    existing proxy deployment working — a fronting proxy reaches Lore over
    loopback/private, so identity headers from that origin are honored with no new
    config. (Upgrading must not silently 401 a working browser session.)"""
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_proxy_auth = True  # no secret, no CIDRs -> private/loopback default
    app = create_app(config)

    headers = {"X-Axis-User": "alice"}
    with TestClient(app, client=("172.17.0.1", 12345)) as client:  # Docker bridge gateway
        loopback_read = client.get("/api/pages", headers=headers)
    assert loopback_read.status_code == 200


def test_trusted_proxy_default_origin_still_blocks_public_source(content_dir, search_db, tmp_path):
    """Secure by default: the private-origin fallback must still reject the
    X-Axis-Admin spoof from a public source IP (the hole flow_000853 closed)."""
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    config.trusted_proxy_auth = True  # no secret, no CIDRs -> private/loopback default
    app = create_app(config)

    headers = {"X-Axis-User": "attacker", "X-Axis-Admin": "1"}
    with TestClient(app, client=("203.0.113.7", 12345)) as client:  # public IP
        response = client.get("/api/api-keys", headers=headers)

    assert response.status_code == 401


def test_policy_write_requires_admin(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _, writer_key = store.create_key(name="writer", role="writer")
    _, admin_key = store.create_key(name="admin", role="admin")
    app = create_app(config)

    policy = {"policy_id": "test-gate:v1", "name": "Test Gate", "gate": "auto-apply"}
    writer_headers = {"Authorization": f"Bearer {writer_key}"}
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    with TestClient(app) as client:
        assert client.post("/api/policies", json=policy, headers=writer_headers).status_code == 403
        assert client.delete("/api/policies/test-gate:v1", headers=writer_headers).status_code == 403

        created = client.post("/api/policies", json=policy, headers=admin_headers)
        assert created.status_code == 200, created.text
        deleted = client.delete("/api/policies/test-gate:v1", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


def test_audit_and_config_require_admin(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _, writer_key = store.create_key(name="writer", role="writer")
    _, admin_key = store.create_key(name="admin", role="admin")
    app = create_app(config)

    writer_headers = {"Authorization": f"Bearer {writer_key}"}
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    with TestClient(app) as client:
        assert client.get("/api/audit", headers=writer_headers).status_code == 403
        assert client.get("/api/config", headers=writer_headers).status_code == 403
        assert client.get("/api/audit", headers=admin_headers).status_code == 200
        assert client.get("/api/config", headers=admin_headers).status_code == 200


def test_session_helpers_sign_and_verify_roundtrip():
    from lore_app.session import sign_session, verify_session

    secret = "session-test-secret"
    token = sign_session(secret, "alice", "reader")
    assert verify_session(secret, token) == ("alice", "reader")

    # Tampered signature is rejected.
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert verify_session(secret, tampered) is None

    # Expired token is rejected.
    expired = sign_session(secret, "alice", "reader", ttl_seconds=-10)
    assert verify_session(secret, expired) is None

    # Wrong secret is rejected.
    assert verify_session("other-secret", token) is None
    # Malformed token never raises.
    assert verify_session(secret, "not-a-token") is None


def test_browser_session_cookie_reaches_reader_and_read_api(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _, reader_key = store.create_key(name="reader", role="reader")
    app = create_app(config)

    with TestClient(app) as client:
        # No cookie yet: the reader HTML route and read API are protected.
        assert client.get("/").status_code == 401

        login = client.post("/api/login", json={"api_key": reader_key})
        assert login.status_code == 200
        assert "lore_session" in login.cookies

        # Cookie jar now authorizes reads without an Authorization header.
        assert client.get("/").status_code == 200
        assert client.get("/api/graph/enriched").status_code == 200

        # Writes stay token-only: a cookie-only write is rejected.
        write = client.put("/api/pages/services/cookie-write", json={"content": "# Cookie Write"})
        assert write.status_code == 401


def test_session_cookie_cannot_write_via_mcp(content_dir, search_db, tmp_path):
    """A session cookie must not authorize writes through POST /mcp.

    Regression for the cross-surface write-boundary bypass: /mcp dispatches both
    read and write tools, so the session cookie (read-only) must not authorize it.
    """
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _, writer_key = store.create_key(name="writer", role="writer")
    app = create_app(config)

    upsert_call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lore_upsert_page",
            "arguments": {
                "page_id": "services/cookie-mcp-write",
                "content": "---\ntitle: Cookie MCP Write\nkind: service\nvisibility: internal\n---\n\n# x\n",
            },
        },
    }

    with TestClient(app) as client:
        assert client.post("/api/login", json={"api_key": writer_key}).status_code == 200

        # Cookie-only POST /mcp must be rejected before the write tool can run.
        resp = client.post("/mcp", json=upsert_call)
        assert resp.status_code == 401

        # The page must not have been created.
        check = client.get(
            "/api/pages/services/cookie-mcp-write",
            headers={"Authorization": f"Bearer {writer_key}"},
        )
        assert check.status_code == 404


def test_cookie_session_get_recall_does_not_stamp_access(content_dir, search_db, tmp_path):
    """A cookie-authenticated GET recall must not perform the record_access state
    write; only a token-authenticated caller may stamp access telemetry."""
    from lore_app.schemas import ExtractedClaim, ExtractionResult

    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _, writer_key = store.create_key(name="recall-writer", role="writer")
    app = create_app(config)
    app.state.ledger_db.store_extraction_result(
        ExtractionResult(
            batch_id="batch-recall-cookie",
            processed_at="2026-05-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-01/recall-cookie"],
            claims=[
                ExtractedClaim(
                    subject="services/recall",
                    predicate="states",
                    object="recall telemetry.",
                    confidence="high",
                    actor="recall-writer",
                )
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )

    token_headers = {"Authorization": f"Bearer {writer_key}"}

    with TestClient(app) as client:
        assert client.post("/api/login", json={"api_key": writer_key}).status_code == 200

        # Cookie-only GET recall asking for record_access must NOT stamp.
        cookie_recall = client.get("/api/memory/recall", params={"record_access": "true"})
        assert cookie_recall.status_code == 200
        assert cookie_recall.json()["count"] >= 1

        # Verify via a token read (no stamping): access_count is still 0.
        after_cookie = client.get("/api/memory/recall", params={"record_access": "false"}, headers=token_headers)
        assert after_cookie.json()["claims"][0]["access_count"] == 0

        # A token-authenticated GET recall WITH record_access still stamps.
        client.get("/api/memory/recall", params={"record_access": "true"}, headers=token_headers)
        after_token = client.get("/api/memory/recall", params={"record_access": "false"}, headers=token_headers)
        assert after_token.json()["claims"][0]["access_count"] >= 1


def test_login_rejects_invalid_key_and_writes_stay_token_only(content_dir, search_db, tmp_path):
    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _, writer_key = store.create_key(name="writer", role="writer")
    app = create_app(config)

    with TestClient(app) as client:
        bad = client.post("/api/login", json={"api_key": "lore_not_a_real_key"})
        assert bad.status_code == 401
        assert "lore_session" not in bad.cookies

        ok = client.post("/api/login", json={"api_key": writer_key})
        assert ok.status_code == 200

        # Even a writer session cookie cannot authorize a write (token-only).
        write = client.put("/api/pages/services/writer-cookie", json={"content": "# x"})
        assert write.status_code == 401
        # But the cookie does authorize reads.
        assert client.get("/api/pages").status_code == 200


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
        recall = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "lore_recall", "arguments": {"query": "test"}},
            },
            headers=headers,
        )
        read_page = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "lore_read_page", "arguments": {"page_id": "concepts/test"}},
            },
            headers=headers,
        )
        # Reader is blocked from a write tool (lore_capture)
        write = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "lore_capture", "arguments": {"text": "test"}},
            },
            headers=headers,
        )

    assert init.status_code == 200
    assert tool_list.status_code == 200
    assert search.status_code == 200
    assert recall.status_code == 200
    assert read_page.status_code == 200
    assert write.status_code == 403


def test_reader_mcp_authorization_follows_every_tool_annotation(content_dir, search_db, tmp_path, monkeypatch):
    from lore_app.mcp.tools import READ_TOOL_NAMES, TOOL_HANDLERS, WRITE_TOOL_NAMES

    def fake_handler(_ctx):
        return {"content": [], "structuredContent": {}, "isError": False}

    for name in TOOL_HANDLERS:
        monkeypatch.setitem(TOOL_HANDLERS, name, fake_handler)

    config = make_config(content_dir, search_db, tmp_path, "api_key", "")
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    _admin_key_obj, admin_key = store.create_key(name="admin-key", role="admin")
    app = create_app(config)

    with TestClient(app) as client:
        created = client.post(
            "/api/api-keys",
            json={"name": "reader", "role": "reader"},
            headers={"Authorization": f"Bearer {admin_key}"},
        ).json()
        headers = {"Authorization": f"Bearer {created['api_key']}"}

        read_statuses = {
            name: client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {}},
                },
                headers=headers,
            ).status_code
            for index, name in enumerate(sorted(READ_TOOL_NAMES), start=1)
        }
        write_statuses = {
            name: client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {}},
                },
                headers=headers,
            ).status_code
            for index, name in enumerate(sorted(WRITE_TOOL_NAMES), start=100)
        }
        unknown = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 999,
                "method": "tools/call",
                "params": {"name": "lore_not_registered", "arguments": {}},
            },
            headers=headers,
        )

    assert set(read_statuses.values()) == {200}, read_statuses
    assert set(write_statuses.values()) == {403}, write_statuses
    assert unknown.status_code == 403


def test_reader_mcp_rejects_whitespace_padded_write_tools(content_dir, search_db, tmp_path):
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

        padded_writes = [
            client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {"name": padded_name, "arguments": {"text": "test"}},
                },
                headers=headers,
            )
            for index, padded_name in enumerate(("lore_capture ", " lore_capture", "\tlore_capture\t"), start=1)
        ]
        batch = client.post(
            "/mcp",
            json=[
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "lore_capture ", "arguments": {"text": "test"}},
                }
            ],
            headers=headers,
        )

    assert [response.status_code for response in padded_writes] == [403, 403, 403]
    assert batch.status_code == 403
