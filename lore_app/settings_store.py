from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .db_utils import retry_on_locked

SETTINGS_LLM_PROVIDER = "llm_provider"
SETTINGS_LLM_MODEL = "llm_model"
SETTINGS_LLM_EMBEDDING_MODEL = "llm_embedding_model"
SETTINGS_LLM_BASE_URL = "llm_base_url"
SETTINGS_LLM_API_KEY = "llm_api_key"
SETTINGS_LLM_ESCALATION_MODEL = "llm_escalation_model"
SETTINGS_LLM_ESCALATION_API_KEY = "llm_escalation_api_key"
SETTINGS_LLM_MAX_TOKENS = "llm_max_tokens"
SETTINGS_LLM_TEMPERATURE = "llm_temperature"
SETTINGS_LLM_TIMEOUT_SECONDS = "llm_timeout_seconds"
SETTINGS_LLM_MAX_RETRIES = "llm_max_retries"

LLM_SETTINGS_KEYS = (
    SETTINGS_LLM_PROVIDER,
    SETTINGS_LLM_MODEL,
    SETTINGS_LLM_EMBEDDING_MODEL,
    SETTINGS_LLM_BASE_URL,
    SETTINGS_LLM_API_KEY,
    SETTINGS_LLM_ESCALATION_MODEL,
    SETTINGS_LLM_ESCALATION_API_KEY,
    SETTINGS_LLM_MAX_TOKENS,
    SETTINGS_LLM_TEMPERATURE,
    SETTINGS_LLM_TIMEOUT_SECONDS,
    SETTINGS_LLM_MAX_RETRIES,
)
SECRET_SETTINGS_KEYS = {
    SETTINGS_LLM_API_KEY,
    SETTINGS_LLM_ESCALATION_API_KEY,
}

SETTINGS_MAINTENANCE_ENABLED = "maintenance_enabled"
SETTINGS_MAINTENANCE_INTERVAL_SECONDS = "maintenance_interval_seconds"

# Background-maintenance scheduler controls. Non-secret, so they surface in
# get_all()/get_all_masked() in plaintext (they are not sensitive).
MAINTENANCE_SETTINGS_KEYS = (
    SETTINGS_MAINTENANCE_ENABLED,
    SETTINGS_MAINTENANCE_INTERVAL_SECONDS,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    # Fully mask short secrets: value[-4:] on a <=4-char secret would echo the
    # whole thing, and 5-8 chars exposes most of it.
    if len(value) <= 8:
        return "****"
    return "****" + value[-4:]


class SettingsStore:
    """SQLite-backed runtime settings store."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._conn_lock = threading.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        with self._conn_lock:
            if self._connection is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA busy_timeout = 5000")
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._set_db_permissions()
        return self._connection

    def initialize(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    secret INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self.connection.commit()
            self._set_db_permissions()

    def get(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ? LIMIT 1",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    def get_masked(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value, secret FROM runtime_settings WHERE key = ? LIMIT 1",
            (key,),
        ).fetchone()
        if row is None:
            return None
        value = str(row["value"])
        return mask_secret(value) if int(row["secret"]) == 1 else value

    @retry_on_locked()
    def set(self, key: str, value: str, secret: bool = False) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO runtime_settings (key, value, secret, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, value, 1 if secret else 0, utc_now()),
            )
            self.connection.commit()
            self._set_db_permissions()

    def get_all(self) -> dict[str, str]:
        rows = self.connection.execute(
            "SELECT key, value FROM runtime_settings WHERE secret = 0 ORDER BY key"
        ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def get_all_masked(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT key, value, secret FROM runtime_settings ORDER BY key").fetchall()
        return {
            str(row["key"]): str(mask_secret(str(row["value"])) if int(row["secret"]) == 1 else row["value"])
            for row in rows
        }

    @retry_on_locked()
    def delete(self, key: str) -> bool:
        with self._lock:
            cursor = self.connection.execute("DELETE FROM runtime_settings WHERE key = ?", (key,))
            self.connection.commit()
            return cursor.rowcount > 0

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _set_db_permissions(self) -> None:
        if self.db_path.exists():
            os.chmod(self.db_path, 0o600)
        wal_path = self.db_path.with_suffix(".db-wal")
        shm_path = self.db_path.with_suffix(".db-shm")
        for sidecar_path in (wal_path, shm_path):
            if sidecar_path.exists():
                os.chmod(sidecar_path, 0o600)
