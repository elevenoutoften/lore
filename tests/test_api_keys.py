from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from lore_app.api_keys import LoreApiKeyStore
from lore_app.config import LoreConfig
from lore_app.main import create_app


@pytest.fixture()
def admin_client(content_dir, search_db):
    """A client in api_key auth mode, where the admin gate is actually enforced.

    (In auth_mode=none the keys/settings pages are intentionally open to the local
    operator, so admin gating can only be exercised with auth enabled.)
    """
    cfg = LoreConfig()
    cfg.content_dir = content_dir
    cfg.search_db = search_db
    cfg.vector_db = search_db.with_name("vectors.db")
    cfg.ledger_db = search_db.with_name("ledger.db")
    cfg.api_keys_db = search_db.with_name("api_keys.db")
    cfg.settings_db = search_db.with_name("settings.db")
    cfg.auth_mode = "api_key"
    cfg.auto_consolidate = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def test_api_key_store_hardens_db_permissions(tmp_path):
    db_path = tmp_path / "api_keys.db"
    store = LoreApiKeyStore(db_path)
    store.initialize()
    store.create_key(name="codex", role="writer")

    if os.name != "nt":
        assert oct(os.stat(db_path).st_mode & 0o777) == "0o600"
        wal_path = db_path.with_suffix(".db-wal")
        if wal_path.exists():
            assert oct(os.stat(wal_path).st_mode & 0o777) == "0o600"

    store.close()


def test_api_key_management_requires_admin(admin_client):
    store = admin_client.app.state.api_key_store
    _key_obj, raw_key = store.create_key(name="test-admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {raw_key}"}

    assert admin_client.get("/api/api-keys").status_code == 401
    assert admin_client.post("/api/api-keys", json={"name": "codex"}).status_code == 401

    created = admin_client.post("/api/api-keys", json={"name": "codex", "role": "writer"}, headers=admin_headers)
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["name"] == "codex"
    assert payload["role"] == "writer"
    assert payload["api_key"].startswith("lore_")
    assert payload["key_prefix"] == payload["api_key"][:16]

    listed = admin_client.get("/api/api-keys", headers=admin_headers)
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 2
    codex_item = next(item for item in items if item["name"] == "codex")
    assert "api_key" not in codex_item
    assert codex_item["key_prefix"] == payload["key_prefix"]


def test_lore_admin_key_can_manage_keys(admin_client):
    store = admin_client.app.state.api_key_store
    _admin_key_obj, admin_key = store.create_key(name="key-admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    created_writer = admin_client.post("/api/api-keys", json={"name": "writer"}, headers=admin_headers)
    assert created_writer.status_code == 201, created_writer.text
    assert admin_client.get("/api/api-keys", headers=admin_headers).status_code == 200

    revoked = admin_client.post(f"/api/api-keys/{created_writer.json()['id']}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_lore_writer_key_cannot_manage_keys(admin_client):
    store = admin_client.app.state.api_key_store
    _admin_key_obj, admin_key = store.create_key(name="key-admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    created_writer = admin_client.post(
        "/api/api-keys",
        json={"name": "writer", "role": "writer"},
        headers=admin_headers,
    ).json()
    writer_headers = {"Authorization": f"Bearer {created_writer['api_key']}"}

    listed = admin_client.get("/api/api-keys", headers=writer_headers)
    created = admin_client.post("/api/api-keys", json={"name": "nested"}, headers=writer_headers)

    assert listed.status_code == 403
    assert created.status_code == 403


def test_local_none_mode_opens_key_and_settings_management(client):
    """On a default local install (auth_mode=none) the loopback operator manages keys/settings."""
    created = client.post("/api/api-keys", json={"name": "first-admin", "role": "admin"})
    assert created.status_code == 201, created.text
    assert created.json()["api_key"].startswith("lore_")
    assert client.get("/api/api-keys").status_code == 200
    assert client.get("/api/settings/llm").status_code == 200


def _configured_client(content_dir, search_db, **overrides) -> TestClient:
    cfg = LoreConfig()
    cfg.content_dir = content_dir
    cfg.search_db = search_db
    cfg.vector_db = search_db.with_name("vectors.db")
    cfg.ledger_db = search_db.with_name("ledger.db")
    cfg.api_keys_db = search_db.with_name("api_keys.db")
    cfg.settings_db = search_db.with_name("settings.db")
    cfg.auto_consolidate = False
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return TestClient(create_app(cfg))


def test_bearer_mode_global_secret_operator_is_admin(content_dir, search_db):
    """The bearer global-secret operator can run the documented keys/settings bootstrap."""
    with _configured_client(content_dir, search_db, auth_mode="bearer", auth_secret="strong-secret-123") as c:
        headers = {"Authorization": "Bearer strong-secret-123"}
        # No token / wrong token: dead-ends as before.
        assert c.get("/api/settings/llm").status_code == 401
        # The trusted operator can read settings and mint admin keys from the UI.
        assert c.get("/api/settings/llm", headers=headers).status_code == 200
        created = c.post("/api/api-keys", json={"name": "first-admin", "role": "admin"}, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["api_key"].startswith("lore_")


def test_insecure_none_bind_denies_nonloopback_admin(content_dir, search_db):
    """auth='none' on a non-loopback bind must not hand admin to a non-loopback client."""
    with _configured_client(
        content_dir, search_db, auth_mode="none", host="0.0.0.0", allow_insecure_bind=True
    ) as c:
        # TestClient is a non-loopback client ("testclient"); the open none-mode bypass
        # must not apply, or any network client could mint keys / rewrite settings.
        assert c.post("/api/api-keys", json={"name": "evil", "role": "admin"}).status_code == 401
        assert c.get("/api/api-keys").status_code == 401
        assert c.get("/api/settings/llm").status_code == 401
