from __future__ import annotations

import importlib
import json

import pytest

from lore_app.config import LoreConfig
from lore_app.main import create_app


def test_config_defaults(monkeypatch):
    for key in [
        "LORE_APP_NAME",
        "LORE_APP_DESCRIPTION",
        "LORE_CONTENT_DIR",
        "LORE_SEARCH_DB",
        "LORE_VECTOR_DB",
        "LORE_API_KEYS_DB",
        "LORE_HOST",
        "LORE_PORT",
        "LORE_AUTH_MODE",
        "LORE_AUTH_SECRET",
        "LORE_METRICS_PUBLIC",
        "LORE_BRAND_TITLE",
        "LORE_BRAND_URL",
        "LORE_FAVICON_URL",
        "LORE_WRITE_RATE_LIMIT",
        "LORE_WRITE_RATE_WINDOW_SECONDS",
        "LORE_AUDIT_RETENTION_DAYS",
        "LORE_CSP_POLICY",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = LoreConfig()
    assert config.app_name == "Lore"
    assert config.content_dir.as_posix() == "data/pages"
    assert config.search_db.as_posix() == "data/db/search.db"
    assert config.vector_db.as_posix() == "data/db/vectors.db"
    assert config.api_keys_db.as_posix() == "data/db/api_keys.db"
    assert config.port == 8000
    # Loopback by default so the default (auth_mode=none) config starts without
    # tripping the insecure-bind guard.
    assert config.host == "127.0.0.1"
    assert config.auth_mode == "none"
    assert config.metrics_public is False
    assert config.brand_title == "LORE"
    assert config.write_rate_limit == 300
    assert config.write_rate_window_seconds == 60
    assert config.audit_retention_days == 365
    assert config.csp_policy == ""
    assert config.llm_provider == "none"
    assert config.llm_model == ""
    assert config.llm_base_url == ""
    assert config.llm_timeout_seconds == 60.0
    assert config.llm_max_retries == 3


def test_config_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_APP_NAME", "Team Lore")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vectors.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    monkeypatch.setenv("LORE_PORT", "9000")
    monkeypatch.setenv("LORE_AUTH_MODE", "bearer")
    monkeypatch.setenv("LORE_METRICS_PUBLIC", "true")
    monkeypatch.setenv("LORE_BRAND_TITLE", "TEAM")
    monkeypatch.setenv("LORE_WRITE_RATE_LIMIT", "12")
    monkeypatch.setenv("LORE_WRITE_RATE_WINDOW_SECONDS", "34")
    monkeypatch.setenv("LORE_AUDIT_RETENTION_DAYS", "56")
    monkeypatch.setenv("LORE_CSP_POLICY", "default-src 'none'")

    payload = LoreConfig().to_dict()
    assert payload["app_name"] == "Team Lore"
    assert payload["content_dir"] == str(tmp_path / "pages")
    assert payload["search_db"] == str(tmp_path / "search.db")
    assert payload["vector_db"] == str(tmp_path / "vectors.db")
    assert payload["api_keys_db"] == str(tmp_path / "api_keys.db")
    assert payload["port"] == 9000
    assert payload["auth_mode"] == "bearer"
    assert payload["metrics_public"] is True
    assert payload["brand_title"] == "TEAM"
    assert payload["write_rate_limit"] == 12
    assert payload["write_rate_window_seconds"] == 34
    assert payload["audit_retention_days"] == 56
    assert payload["csp_policy"] == "default-src 'none'"
    assert payload["llm_timeout_seconds"] == 60.0
    assert payload["llm_max_retries"] == 3


def test_config_workspaces_env(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "LORE_WORKSPACES",
        json.dumps(
            {
                "team": {
                    "content_dir": str(tmp_path / "team-pages"),
                    "search_db": str(tmp_path / "team-search.db"),
                    "vector_db": str(tmp_path / "team-vector.db"),
                }
            }
        ),
    )

    config = LoreConfig()
    assert set(config.workspaces) == {"team"}
    assert config.workspaces["team"].content_dir == tmp_path / "team-pages"

    payload = config.to_dict()
    assert payload["workspaces"]["team"]["search_db"] == str(tmp_path / "team-search.db")


def test_config_invalid_auth_mode_raises(monkeypatch):
    monkeypatch.setenv("LORE_AUTH_MODE", "bearerr")
    with pytest.raises(ValueError, match="Unsupported"):
        LoreConfig()


def test_config_valid_auth_modes(monkeypatch):
    for mode in ("none", "bearer", "basic", "api_key"):
        monkeypatch.setenv("LORE_AUTH_MODE", mode)
        config = LoreConfig()
        assert config.auth_mode == mode
        monkeypatch.delenv("LORE_AUTH_MODE")


def test_api_config_endpoint(content_dir, search_db, tmp_path):
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = search_db
    config.vector_db = tmp_path / "vectors.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["content_dir"] == str(content_dir)


def test_insecure_bind_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "none")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    monkeypatch.delenv("LORE_ALLOW_INSECURE_BIND", raising=False)
    with pytest.raises(ValueError, match="SECURITY"):
        create_app()


def test_import_main_does_not_bypass_insecure_bind_guard(monkeypatch, tmp_path):
    """Regression: importing lore_app.main must not set LORE_ALLOW_INSECURE_BIND."""
    monkeypatch.setenv("LORE_AUTH_MODE", "none")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")
    monkeypatch.delenv("LORE_ALLOW_INSECURE_BIND", raising=False)
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    import lore_app.main as _main_mod

    importlib.reload(_main_mod)

    with pytest.raises(ValueError, match="SECURITY"):
        _main_mod.create_app()


def test_insecure_bind_allowed_with_override(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "none")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")
    monkeypatch.setenv("LORE_ALLOW_INSECURE_BIND", "true")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    app = create_app()
    assert app is not None


def test_loopback_bind_allowed_without_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "none")
    monkeypatch.setenv("LORE_HOST", "127.0.0.1")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    monkeypatch.delenv("LORE_ALLOW_INSECURE_BIND", raising=False)
    app = create_app()
    assert app is not None


def test_localhost_bind_allowed_without_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "none")
    monkeypatch.setenv("LORE_HOST", "localhost")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    monkeypatch.delenv("LORE_ALLOW_INSECURE_BIND", raising=False)
    app = create_app()
    assert app is not None


def test_known_placeholder_secret_rejected_on_non_loopback(monkeypatch):
    monkeypatch.setenv("LORE_AUTH_MODE", "bearer")
    monkeypatch.setenv("LORE_AUTH_SECRET", "change-me-in-production")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")

    with pytest.raises(ValueError, match="SECURITY"):
        LoreConfig()


def test_known_placeholder_secret_allowed_on_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "bearer")
    monkeypatch.setenv("LORE_AUTH_SECRET", "change-me")
    monkeypatch.setenv("LORE_HOST", "127.0.0.1")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))

    app = create_app()
    assert app is not None


def test_real_secret_allowed_on_non_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "bearer")
    monkeypatch.setenv("LORE_AUTH_SECRET", "a-real-opaque-secret")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    monkeypatch.delenv("LORE_ALLOW_INSECURE_BIND", raising=False)

    app = create_app()
    assert app is not None


def test_empty_secret_rejected_in_bearer_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "bearer")
    monkeypatch.setenv("LORE_AUTH_SECRET", "")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))

    with pytest.raises(ValueError, match="non-empty string"):
        create_app()


def test_placeholder_secret_case_insensitive(monkeypatch):
    monkeypatch.setenv("LORE_AUTH_MODE", "bearer")
    monkeypatch.setenv("LORE_AUTH_SECRET", "Change-Me")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")

    with pytest.raises(ValueError, match="SECURITY"):
        LoreConfig()


def test_auth_mode_does_not_trigger_bind_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_AUTH_MODE", "bearer")
    monkeypatch.setenv("LORE_AUTH_SECRET", "a-real-opaque-secret")
    monkeypatch.setenv("LORE_HOST", "0.0.0.0")
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    monkeypatch.delenv("LORE_ALLOW_INSECURE_BIND", raising=False)
    app = create_app()
    assert app is not None
