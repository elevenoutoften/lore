"""Lore product configuration - all customizable defaults in one place."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm_provider import LLMProviderConfig
from .settings_store import (
    SETTINGS_LLM_API_KEY,
    SETTINGS_LLM_BASE_URL,
    SETTINGS_LLM_EMBEDDING_MODEL,
    SETTINGS_LLM_ESCALATION_API_KEY,
    SETTINGS_LLM_ESCALATION_MODEL,
    SETTINGS_LLM_MAX_RETRIES,
    SETTINGS_LLM_MAX_TOKENS,
    SETTINGS_LLM_MODEL,
    SETTINGS_LLM_PROVIDER,
    SETTINGS_LLM_TEMPERATURE,
    SETTINGS_LLM_TIMEOUT_SECONDS,
    SettingsStore,
)

VALID_AUTH_MODES = ("none", "bearer", "basic", "api_key")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
KNOWN_INSECURE_SECRETS = frozenset(
    {
        "change-me",
        "changeme",
        "change-me-in-production",
        "your-secret-here",
        "secret",
        "password",
        "default",
    }
)


@dataclass(frozen=True)
class WorkspaceConfig:
    """Optional per-workspace storage overrides."""

    content_dir: Path | None = None
    search_db: Path | None = None
    vector_db: Path | None = None
    ledger_db: Path | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> WorkspaceConfig:
        return cls(
            content_dir=Path(payload["content_dir"]) if payload.get("content_dir") else None,
            search_db=Path(payload["search_db"]) if payload.get("search_db") else None,
            vector_db=Path(payload["vector_db"]) if payload.get("vector_db") else None,
            ledger_db=Path(payload["ledger_db"]) if payload.get("ledger_db") else None,
        )

    def resolve(self, base: LoreConfig) -> WorkspaceConfig:
        return WorkspaceConfig(
            content_dir=self.content_dir or base.content_dir,
            search_db=self.search_db or base.search_db,
            vector_db=self.vector_db or base.vector_db,
            ledger_db=self.ledger_db or base.ledger_db,
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "content_dir": str(self.content_dir) if self.content_dir else None,
            "search_db": str(self.search_db) if self.search_db else None,
            "vector_db": str(self.vector_db) if self.vector_db else None,
            "ledger_db": str(self.ledger_db) if self.ledger_db else None,
        }


class LoreConfig:
    """Centralized configuration with env-var overrides."""

    def __init__(self) -> None:
        self.data_dir: Path = Path(os.environ.get("LORE_DATA_DIR", "./data"))
        default_db_dir = self.data_dir / "db"
        self.app_name: str = os.environ.get("LORE_APP_NAME", "Lore")
        self.app_description: str = os.environ.get(
            "LORE_APP_DESCRIPTION",
            "Markdown-backed knowledge wiki for teams and agents.",
        )
        self.content_dir: Path = Path(os.environ.get("LORE_CONTENT_DIR", "./data/pages"))
        self.search_db: Path = Path(os.environ.get("LORE_SEARCH_DB", str(default_db_dir / "search.db")))
        self.vector_db: Path = Path(os.environ.get("LORE_VECTOR_DB", str(default_db_dir / "vectors.db")))
        self.ledger_db: Path = Path(os.environ.get("LORE_LEDGER_DB", str(default_db_dir / "ledger.db")))
        self.api_keys_db: Path = Path(os.environ.get("LORE_API_KEYS_DB", str(default_db_dir / "api_keys.db")))
        self.settings_db: Path = Path(os.environ.get("LORE_SETTINGS_DB", str(default_db_dir / "settings.db")))
        # Default to loopback so the documented bare-metal quickstart starts out of
        # the box (auth_mode=none + a non-loopback bind trips the insecure-bind guard).
        # Production (Docker/systemd) overrides LORE_HOST=0.0.0.0 with auth enabled.
        self.host: str = os.environ.get("LORE_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("LORE_PORT", "8000"))
        self.auth_mode: str = os.environ.get("LORE_AUTH_MODE", "none")
        self.auth_secret: str = os.environ.get("LORE_AUTH_SECRET", "")
        self.brand_title: str = os.environ.get("LORE_BRAND_TITLE", "LORE")
        self.brand_url: str = os.environ.get("LORE_BRAND_URL", "/")
        self.favicon_url: str = os.environ.get("LORE_FAVICON_URL", "/static/lore.css")
        self.write_rate_limit: int = int(os.environ.get("LORE_WRITE_RATE_LIMIT", "300"))
        self.write_rate_window_seconds: int = int(os.environ.get("LORE_WRITE_RATE_WINDOW_SECONDS", "60"))
        # Auto-consolidate captures in the background so the capture -> recall loop
        # is non-empty out of the box without a manual consolidation step. Disable
        # (LORE_AUTO_CONSOLIDATE=false) for very high write volume + a scheduled runner.
        self.auto_consolidate: bool = os.environ.get("LORE_AUTO_CONSOLIDATE", "true").lower() not in (
            "false",
            "0",
            "no",
            "off",
        )
        self.audit_retention_days: int = int(os.environ.get("LORE_AUDIT_RETENTION_DAYS", "365"))
        self.allow_insecure_bind: bool = os.environ.get("LORE_ALLOW_INSECURE_BIND", "").lower() in ("true", "1", "yes")
        self.trusted_headers: bool = os.environ.get("LORE_TRUSTED_HEADERS", "").lower() in ("true", "1", "yes")
        self.trusted_proxy_auth: bool = os.environ.get("LORE_TRUSTED_PROXY_AUTH", "").lower() in ("true", "1", "yes")
        self.csp_policy: str = os.environ.get("LORE_CSP_POLICY", "")
        self.embed_frame_ancestors: list[str] = [
            origin.strip()
            for origin in os.environ.get("LORE_EMBED_FRAME_ANCESTORS", "").replace(",", " ").split()
            if origin.strip()
        ]
        self.code_ingest_roots: list[Path] = self._parse_roots(os.environ.get("LORE_CODE_INGEST_ROOTS", ""))
        self.code_ingest_max_files: int = int(os.environ.get("LORE_CODE_INGEST_MAX_FILES", "500"))
        self.code_ingest_max_depth: int = int(os.environ.get("LORE_CODE_INGEST_MAX_DEPTH", "10"))
        self.code_ingest_max_total_bytes: int = int(
            os.environ.get("LORE_CODE_INGEST_MAX_TOTAL_BYTES", str(50 * 1024 * 1024))
        )  # 50 MiB
        self.llm_provider: str = os.environ.get("LORE_LLM_PROVIDER", "none")
        self.llm_model: str = os.environ.get("LORE_LLM_MODEL", "")
        self.llm_embedding_model: str = os.environ.get("LORE_LLM_EMBEDDING_MODEL", "")
        self.llm_base_url: str = os.environ.get("LORE_LLM_BASE_URL", "")
        self.llm_api_key: str | None = os.environ.get("LORE_LLM_API_KEY")
        self.llm_max_tokens: int = int(os.environ.get("LORE_LLM_MAX_TOKENS", "4096"))
        self.llm_temperature: float = float(os.environ.get("LORE_LLM_TEMPERATURE", "0.3"))
        self.llm_timeout_seconds: float = float(os.environ.get("LORE_LLM_TIMEOUT", "60"))
        self.llm_max_retries: int = int(os.environ.get("LORE_LLM_MAX_RETRIES", "3"))
        self.llm_escalation_model: str = os.environ.get("LORE_LLM_ESCALATION_MODEL", "minimax-m3")
        self.llm_escalation_api_key: str | None = os.environ.get("LORE_LLM_ESCALATION_API_KEY")
        self.workspaces: dict[str, WorkspaceConfig] = parse_workspaces(os.environ.get("LORE_WORKSPACES"))
        if self.auth_mode not in VALID_AUTH_MODES:
            raise ValueError(
                f"Unsupported LORE_AUTH_MODE={self.auth_mode!r}. Must be one of: {', '.join(VALID_AUTH_MODES)}."
            )
        if self.auth_mode in ("bearer", "basic"):
            secret_lower = self.auth_secret.strip().lower()
            if secret_lower in KNOWN_INSECURE_SECRETS and self.host not in LOOPBACK_HOSTS:
                raise ValueError(
                    f"SECURITY: LORE_AUTH_SECRET is a known placeholder value ({self.auth_secret!r}). "
                    f"A non-loopback bind address ({self.host!r}) requires a strong, unique secret. "
                    'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_name": self.app_name,
            "app_description": self.app_description,
            "content_dir": str(self.content_dir),
            "search_db": str(self.search_db),
            "vector_db": str(self.vector_db),
            "ledger_db": str(self.ledger_db),
            "api_keys_db": str(self.api_keys_db),
            "host": self.host,
            "port": self.port,
            "auth_mode": self.auth_mode,
            "brand_title": self.brand_title,
            "write_rate_limit": self.write_rate_limit,
            "write_rate_window_seconds": self.write_rate_window_seconds,
            "audit_retention_days": self.audit_retention_days,
            "trusted_headers": self.trusted_headers,
            "trusted_proxy_auth": self.trusted_proxy_auth,
            "allow_insecure_bind": self.allow_insecure_bind,
            "csp_policy": self.csp_policy,
            "embed_frame_ancestors": list(self.embed_frame_ancestors),
            "code_ingest_roots": [str(r) for r in self.code_ingest_roots],
            "code_ingest_max_files": self.code_ingest_max_files,
            "code_ingest_max_depth": self.code_ingest_max_depth,
            "code_ingest_max_total_bytes": self.code_ingest_max_total_bytes,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_embedding_model": self.llm_embedding_model,
            "llm_base_url": self.llm_base_url,
            "llm_api_key_configured": self.llm_api_key is not None,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_temperature": self.llm_temperature,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_max_retries": self.llm_max_retries,
            "llm_escalation_model": self.llm_escalation_model,
            "llm_escalation_api_key_configured": self.llm_escalation_api_key is not None,
            "workspaces": {name: workspace.to_dict() for name, workspace in self.workspaces.items()},
        }

    @staticmethod
    def _parse_roots(raw: str) -> list[Path]:
        """Parse colon/semicolon-separated rooted paths."""
        if not raw:
            return []
        parts = raw.split(";") if platform.system() == "Windows" else raw.replace(";", ":").split(":")
        paths = []
        for part in parts:
            part = part.strip()
            if part:
                paths.append(Path(part).resolve())
        return paths


def parse_workspaces(raw_value: str | None) -> dict[str, WorkspaceConfig]:
    if not raw_value:
        return {}
    payload = json.loads(raw_value)
    if not isinstance(payload, dict):
        raise ValueError("LORE_WORKSPACES must be a JSON object.")

    workspaces: dict[str, WorkspaceConfig] = {}
    for name, workspace_payload in payload.items():
        clean_name = str(name).strip().strip("/")
        if not clean_name or "/" in clean_name:
            raise ValueError("Workspace names must be single URL path segments.")
        if not isinstance(workspace_payload, dict):
            raise ValueError(f"Workspace {clean_name!r} must be a JSON object.")
        workspaces[clean_name] = WorkspaceConfig.from_mapping(workspace_payload)
    return workspaces


def merged_llm_config(config: LoreConfig, settings_store: SettingsStore) -> LLMProviderConfig:
    """Build LLM config with runtime settings overriding env-derived config."""

    from .llm_provider import DEFAULT_ESCALATION_MODEL, DEFAULT_EXTRACTION_MODEL

    provider = settings_store.get(SETTINGS_LLM_PROVIDER) or config.llm_provider
    model = settings_store.get(SETTINGS_LLM_MODEL) or config.llm_model or DEFAULT_EXTRACTION_MODEL
    embedding_model = settings_store.get(SETTINGS_LLM_EMBEDDING_MODEL) or config.llm_embedding_model
    base_url = settings_store.get(SETTINGS_LLM_BASE_URL) or config.llm_base_url
    api_key = settings_store.get(SETTINGS_LLM_API_KEY) or config.llm_api_key
    escalation_model = (
        settings_store.get(SETTINGS_LLM_ESCALATION_MODEL) or config.llm_escalation_model or DEFAULT_ESCALATION_MODEL
    )
    escalation_api_key = settings_store.get(SETTINGS_LLM_ESCALATION_API_KEY) or config.llm_escalation_api_key

    max_tokens_setting = settings_store.get(SETTINGS_LLM_MAX_TOKENS)
    temperature_setting = settings_store.get(SETTINGS_LLM_TEMPERATURE)
    timeout_setting = settings_store.get(SETTINGS_LLM_TIMEOUT_SECONDS)
    max_retries_setting = settings_store.get(SETTINGS_LLM_MAX_RETRIES)

    return LLMProviderConfig(
        name=provider,
        model=model,
        embedding_model=embedding_model or None,
        base_url=base_url or None,
        api_key=api_key or None,
        max_tokens=int(max_tokens_setting) if max_tokens_setting else config.llm_max_tokens,
        temperature=float(temperature_setting) if temperature_setting else config.llm_temperature,
        timeout_seconds=float(timeout_setting) if timeout_setting else config.llm_timeout_seconds,
        max_retries=int(max_retries_setting) if max_retries_setting else config.llm_max_retries,
        escalation_model=escalation_model or None,
        escalation_api_key=escalation_api_key or None,
    )
