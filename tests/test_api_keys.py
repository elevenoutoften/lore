from __future__ import annotations

import os

from lore_app.api_keys import LoreApiKeyStore


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


def test_api_key_management_requires_admin(client):
    store = client.app.state.api_key_store
    _key_obj, raw_key = store.create_key(name="test-admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {raw_key}"}

    assert client.get("/api/api-keys").status_code == 401
    assert client.post("/api/api-keys", json={"name": "codex"}).status_code == 401

    created = client.post("/api/api-keys", json={"name": "codex", "role": "writer"}, headers=admin_headers)
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["name"] == "codex"
    assert payload["role"] == "writer"
    assert payload["api_key"].startswith("lore_")
    assert payload["key_prefix"] == payload["api_key"][:16]

    listed = client.get("/api/api-keys", headers=admin_headers)
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 2
    codex_item = next(item for item in items if item["name"] == "codex")
    assert "api_key" not in codex_item
    assert codex_item["key_prefix"] == payload["key_prefix"]


def test_lore_admin_key_can_manage_keys(client):
    store = client.app.state.api_key_store
    _admin_key_obj, admin_key = store.create_key(name="key-admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    created_writer = client.post("/api/api-keys", json={"name": "writer"}, headers=admin_headers)
    assert created_writer.status_code == 201, created_writer.text
    assert client.get("/api/api-keys", headers=admin_headers).status_code == 200

    revoked = client.post(f"/api/api-keys/{created_writer.json()['id']}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_lore_writer_key_cannot_manage_keys(client):
    store = client.app.state.api_key_store
    _admin_key_obj, admin_key = store.create_key(name="key-admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    created_writer = client.post(
        "/api/api-keys",
        json={"name": "writer", "role": "writer"},
        headers=admin_headers,
    ).json()
    writer_headers = {"Authorization": f"Bearer {created_writer['api_key']}"}

    listed = client.get("/api/api-keys", headers=writer_headers)
    created = client.post("/api/api-keys", json={"name": "nested"}, headers=writer_headers)

    assert listed.status_code == 403
    assert created.status_code == 403
