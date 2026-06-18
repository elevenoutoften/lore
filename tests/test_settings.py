from __future__ import annotations

import os
import sqlite3

from lore_app.config import LoreConfig, merged_llm_config
from lore_app.llm_provider import FallbackLLMClient
from lore_app.settings_store import (
    SETTINGS_LLM_API_KEY,
    SETTINGS_LLM_BASE_URL,
    SETTINGS_LLM_MAX_TOKENS,
    SETTINGS_LLM_MODEL,
    SettingsStore,
    mask_secret,
)


def test_mask_secret_fully_masks_short_values():
    assert mask_secret("abc") == "****"
    assert mask_secret("sk12") == "****"
    assert mask_secret("12345678") == "****"
    assert mask_secret("sk-runtime-b1f3") == "****b1f3"
    assert mask_secret("") is None
    assert mask_secret(None) is None


def admin_headers(client) -> dict[str, str]:
    _key_obj, raw_key = client.app.state.api_key_store.create_key(name="settings-admin", role="admin")
    return {"Authorization": f"Bearer {raw_key}"}


def reader_headers(client) -> dict[str, str]:
    _key_obj, raw_key = client.app.state.api_key_store.create_key(name="settings-reader", role="reader")
    return {"Authorization": f"Bearer {raw_key}"}


def test_settings_store_crud_and_masking(tmp_path):
    db_path = tmp_path / "settings.db"
    store = SettingsStore(db_path)
    store.initialize()

    store.set(SETTINGS_LLM_MODEL, "glm-5.1")
    store.set(SETTINGS_LLM_API_KEY, "sk-runtime-b1f3", secret=True)

    assert store.get(SETTINGS_LLM_MODEL) == "glm-5.1"
    assert store.get(SETTINGS_LLM_API_KEY) == "sk-runtime-b1f3"
    assert store.get_masked(SETTINGS_LLM_API_KEY) == "****b1f3"
    assert store.get_all() == {SETTINGS_LLM_MODEL: "glm-5.1"}
    assert store.get_all_masked()[SETTINGS_LLM_API_KEY] == "****b1f3"
    if os.name != "nt":
        assert oct(os.stat(db_path).st_mode & 0o777) == "0o600"

    with sqlite3.connect(db_path) as conn:
        secret = conn.execute(
            "SELECT secret FROM runtime_settings WHERE key = ?",
            (SETTINGS_LLM_API_KEY,),
        ).fetchone()[0]
    assert secret == 1

    assert store.delete(SETTINGS_LLM_MODEL) is True
    assert store.get(SETTINGS_LLM_MODEL) is None
    assert store.delete(SETTINGS_LLM_MODEL) is False
    store.close()


def test_put_then_get_llm_settings_masks_secrets(client):
    headers = admin_headers(client)
    payload = {
        "provider": "ollama",
        "model": "glm-5.1",
        "embedding_model": "embeddinggemma",
        "base_url": "https://example.invalid/v1",
        "api_key": "sk-secret-b1f3",
        "escalation_model": "minimax-m3",
        "escalation_api_key": "sk-escalation-c9d2",
        "max_tokens": 2048,
        "temperature": 0.2,
        "timeout_seconds": 30,
        "max_retries": 2,
    }

    updated = client.put("/api/settings/llm", json=payload, headers=headers)
    assert updated.status_code == 200, updated.text
    updated_payload = updated.json()
    assert updated_payload["model"] == "glm-5.1"
    assert updated_payload["embedding_model"] == "embeddinggemma"
    assert updated_payload["embeddings_enabled"] is True
    assert updated_payload["api_key_configured"] is True
    assert updated_payload["api_key_hint"] == "****b1f3"
    assert "sk-secret-b1f3" not in updated.text
    assert "sk-escalation-c9d2" not in updated.text

    fetched = client.get("/api/settings/llm", headers=reader_headers(client))
    assert fetched.status_code == 200, fetched.text
    fetched_payload = fetched.json()
    assert fetched_payload == updated_payload
    assert "api_key" not in fetched_payload
    assert "sk-secret-b1f3" not in fetched.text


def test_hot_reload_swaps_llm_client(client):
    old_client = client.app.state.llm_client
    response = client.put(
        "/api/settings/llm",
        json={"provider": "ollama", "model": "runtime-model", "api_key": "sk-runtime"},
        headers=admin_headers(client),
    )

    assert response.status_code == 200, response.text
    new_client = client.app.state.llm_client
    assert new_client is not old_client
    assert isinstance(new_client, FallbackLLMClient)
    assert new_client.primary.config.model == "runtime-model"


def test_merged_llm_config_uses_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LORE_LLM_MODEL", "glm-5.1")
    monkeypatch.setenv("LORE_LLM_EMBEDDING_MODEL", "embeddinggemma")
    monkeypatch.setenv("LORE_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LORE_LLM_API_KEY", "sk-env")
    config = LoreConfig()
    store = SettingsStore(tmp_path / "settings.db")
    store.initialize()

    merged = merged_llm_config(config, store)

    assert merged.name == "ollama"
    assert merged.model == "glm-5.1"
    assert merged.embedding_model == "embeddinggemma"
    assert merged.base_url == "https://env.example/v1"
    assert merged.api_key == "sk-env"
    store.close()


def test_merged_llm_config_defaults_model_when_unset(tmp_path, monkeypatch):
    """A provider with no model must fall back to the default model, not an empty string."""
    monkeypatch.setenv("LORE_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LORE_LLM_API_KEY", "sk-env")
    monkeypatch.delenv("LORE_LLM_MODEL", raising=False)
    config = LoreConfig()
    store = SettingsStore(tmp_path / "settings.db")
    store.initialize()

    merged = merged_llm_config(config, store)

    assert merged.model == "glm-5.1"
    assert merged.escalation_model == "minimax-m3"
    store.close()


def test_stored_settings_override_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LORE_LLM_MODEL", "glm-5.1")
    config = LoreConfig()
    store = SettingsStore(tmp_path / "settings.db")
    store.initialize()
    store.set(SETTINGS_LLM_MODEL, "minimax-m3")
    store.set(SETTINGS_LLM_MAX_TOKENS, "1024")

    merged = merged_llm_config(config, store)

    assert merged.model == "minimax-m3"
    assert merged.max_tokens == 1024
    store.close()


def test_extraction_uses_configured_model(client, monkeypatch):
    class FakeRuntimeLLMClient:
        def __init__(self, config):
            self.config = config

        def extract_json(self, system_prompt, user_prompt, *, temperature=None, model=None):
            del system_prompt, user_prompt, temperature, model
            return {
                "entities": [{"subject": "services/lore", "name": "Lore", "entity_type": "service"}],
                "claims": [
                    {
                        "subject": "services/lore",
                        "predicate": "states",
                        "object": "Runtime settings select the extraction model.",
                        "confidence": "high",
                        "source_page_ids": [],
                    }
                ],
                "edges": [],
                "invalidations": [],
                "_lore_meta": {"usage": {"prompt_tokens": 1}, "model": self.config.model},
            }

        def close(self):
            pass

    monkeypatch.setattr("lore_app.llm_provider.LLMClient", FakeRuntimeLLMClient)

    settings = client.put(
        "/api/settings/llm",
        json={"provider": "ollama", "model": "configured-model", "api_key": "sk-runtime"},
        headers=admin_headers(client),
    )
    assert settings.status_code == 200, settings.text

    capture = client.post(
        "/api/capture",
        json={
            "title": "Runtime settings capture",
            "observation": "Runtime settings should select the configured model.",
            "suggested_target_page": "services/lore",
        },
    )
    assert capture.status_code == 201, capture.text
    capture_id = capture.json()["page"]["id"]

    extract = client.post("/api/extraction/run", json={"capture_ids": [capture_id], "dry_run": True})
    assert extract.status_code == 200, extract.text
    assert extract.json()["claims"][0]["model_version"] == "configured-model"


def test_settings_security_no_raw_key_in_response_or_audit(client):
    headers = admin_headers(client)
    raw_key = "sk-do-not-return-7788"

    response = client.put(
        "/api/settings/llm",
        json={"provider": "ollama", "model": "secure-model", "api_key": raw_key},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert raw_key not in response.text

    fetched = client.get("/api/settings/llm", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert raw_key not in fetched.text

    audit = client.get("/api/audit", headers=headers)
    assert audit.status_code == 200, audit.text
    assert raw_key not in audit.text


def test_delete_reverts_to_env_and_clears_store(client):
    config = client.app.state.config
    config.llm_provider = "ollama"
    config.llm_model = "env-model"
    config.llm_base_url = "https://env.example/v1"
    config.llm_api_key = None

    headers = admin_headers(client)
    updated = client.put(
        "/api/settings/llm",
        json={"provider": "ollama", "model": "stored-model", "base_url": "https://stored.example/v1"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["model"] == "stored-model"

    deleted = client.delete("/api/settings/llm", headers=headers)
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["model"] == "env-model"
    assert payload["base_url"] == "https://env.example/v1"
    assert payload["api_key_configured"] is False
    assert client.app.state.settings_store.get(SETTINGS_LLM_MODEL) is None
    assert client.app.state.settings_store.get(SETTINGS_LLM_BASE_URL) is None
