"""Lore product configuration - all customizable defaults in one place."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkspaceConfig:
    """Optional per-workspace storage overrides."""

    content_dir: Path | None = None
    search_db: Path | None = None
    vector_db: Path | None = None
    ledger_db: Path | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "WorkspaceConfig":
        return cls(
            content_dir=Path(payload["content_dir"]) if payload.get("content_dir") else None,
            search_db=Path(payload["search_db"]) if payload.get("search_db") else None,
            vector_db=Path(payload["vector_db"]) if payload.get("vector_db") else None,
            ledger_db=Path(payload["ledger_db"]) if payload.get("ledger_db") else None,
        )

    def resolve(self, base: "LoreConfig") -> "WorkspaceConfig":
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
        self.app_name: str = os.environ.get("LORE_APP_NAME", "Lore")
        self.app_description: str = os.environ.get(
            "LORE_APP_DESCRIPTION",
            "Markdown-backed knowledge wiki for teams and agents.",
        )
        self.content_dir: Path = Path(os.environ.get("LORE_CONTENT_DIR", "./data/pages"))
        self.search_db: Path = Path(os.environ.get("LORE_SEARCH_DB", "/data/db/search.db"))
        self.vector_db: Path = Path(os.environ.get("LORE_VECTOR_DB", "/data/db/vectors.db"))
        self.ledger_db: Path = Path(os.environ.get("LORE_LEDGER_DB", "/data/db/ledger.db"))
        self.api_keys_db: Path = Path(os.environ.get("LORE_API_KEYS_DB", "/data/db/api_keys.db"))
        self.host: str = os.environ.get("LORE_HOST", "0.0.0.0")
        self.port: int = int(os.environ.get("LORE_PORT", "8000"))
        self.auth_mode: str = os.environ.get("LORE_AUTH_MODE", "none")
        self.auth_secret: str = os.environ.get("LORE_AUTH_SECRET", "")
        self.brand_title: str = os.environ.get("LORE_BRAND_TITLE", "LORE")
        self.brand_url: str = os.environ.get("LORE_BRAND_URL", "/")
        self.favicon_url: str = os.environ.get("LORE_FAVICON_URL", "/static/lore.css")
        self.write_rate_limit: int = int(os.environ.get("LORE_WRITE_RATE_LIMIT", "30"))
        self.write_rate_window_seconds: int = int(os.environ.get("LORE_WRITE_RATE_WINDOW_SECONDS", "60"))
        self.audit_retention_days: int = int(os.environ.get("LORE_AUDIT_RETENTION_DAYS", "365"))
        self.trusted_headers: bool = os.environ.get("LORE_TRUSTED_HEADERS", "").lower() in ("true", "1", "yes")
        self.csp_policy: str = os.environ.get("LORE_CSP_POLICY", "")
        self.workspaces: dict[str, WorkspaceConfig] = parse_workspaces(os.environ.get("LORE_WORKSPACES"))

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
            "csp_policy": self.csp_policy,
            "workspaces": {name: workspace.to_dict() for name, workspace in self.workspaces.items()},
        }


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
