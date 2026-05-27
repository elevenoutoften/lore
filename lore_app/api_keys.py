from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .db_utils import retry_on_locked

VALID_API_KEY_ROLES = {"admin", "writer", "reader"}


@dataclass(frozen=True)
class LoreApiKey:
    id: str
    name: str
    description: str
    role: str
    key_prefix: str
    created_at: str
    revoked_at: str | None


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key_secret() -> str:
    return "lore_" + secrets.token_urlsafe(32)


class LoreApiKeyStore:
    """SQLite-backed Lore API key registry."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._connection: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        self._lock = threading.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        with self._conn_lock:
            if self._connection is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA busy_timeout = 5000")
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA foreign_keys = ON")
        return self._connection

    def initialize(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'writer',
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT DEFAULT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_lore_api_keys_hash
                    ON api_keys(key_hash);
                CREATE INDEX IF NOT EXISTS idx_lore_api_keys_revoked
                    ON api_keys(revoked_at);
                """
            )
            self.connection.commit()
            self._migrate_columns()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def list_keys(self) -> list[LoreApiKey]:
        rows = self.connection.execute(
            """
            SELECT id, name, description, role, key_prefix, created_at, revoked_at
            FROM api_keys
            ORDER BY revoked_at IS NOT NULL, lower(name), created_at, id
            """
        ).fetchall()
        return [self._row_to_key(row) for row in rows]

    def get_key(self, api_key_id: str) -> LoreApiKey | None:
        row = self.connection.execute(
            """
            SELECT id, name, description, role, key_prefix, created_at, revoked_at
            FROM api_keys
            WHERE id = ?
            LIMIT 1
            """,
            (api_key_id,),
        ).fetchone()
        return self._row_to_key(row) if row else None

    @retry_on_locked()
    def verify_token(self, token: str) -> LoreApiKey | None:
        if not token:
            return None
        row = self.connection.execute(
            """
            SELECT id, name, description, role, key_prefix, created_at, revoked_at
            FROM api_keys
            WHERE key_hash = ? AND revoked_at IS NULL
            LIMIT 1
            """,
            (hash_api_key(token),),
        ).fetchone()
        return self._row_to_key(row) if row else None

    @retry_on_locked()
    def create_key(self, *, name: str, description: str = "", role: str = "writer") -> tuple[LoreApiKey, str]:
        clean_name = " ".join(name.split())
        if not clean_name:
            raise ValueError("API key name is required.")
        clean_description = description.strip()
        clean_role = role.strip() or "writer"
        if clean_role not in VALID_API_KEY_ROLES:
            raise ValueError(f"Invalid API key role: {clean_role}")

        raw_key = generate_api_key_secret()
        now = utc_now()
        with self._lock:
            key_id = self._new_key_id()
            self.connection.execute(
                """
                INSERT INTO api_keys (
                    id, name, description, role, key_prefix, key_hash, created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    key_id,
                    clean_name,
                    clean_description,
                    clean_role,
                    raw_key[:16],
                    hash_api_key(raw_key),
                    now,
                ),
            )
            self.connection.commit()
            created = self.get_key(key_id)
        if created is None:
            raise RuntimeError("Created API key could not be loaded.")
        return created, raw_key

    def revoke_key(self, api_key_id: str) -> LoreApiKey | None:
        with self._lock:
            existing = self.get_key(api_key_id)
            if existing is None:
                return None
            if existing.revoked_at is None:
                self.connection.execute(
                    "UPDATE api_keys SET revoked_at = ? WHERE id = ?",
                    (utc_now(), api_key_id),
                )
                self.connection.commit()
            return self.get_key(api_key_id)

    def _migrate_columns(self) -> None:
        existing = {row[1] for row in self.connection.execute("PRAGMA table_info(api_keys)").fetchall()}
        migrations = {
            "description": "ALTER TABLE api_keys ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            "role": "ALTER TABLE api_keys ADD COLUMN role TEXT NOT NULL DEFAULT 'writer'",
            "key_prefix": "ALTER TABLE api_keys ADD COLUMN key_prefix TEXT NOT NULL DEFAULT ''",
            "revoked_at": "ALTER TABLE api_keys ADD COLUMN revoked_at TEXT DEFAULT NULL",
        }
        for column, statement in migrations.items():
            if column not in existing:
                self.connection.execute(statement)
        self.connection.commit()

    def _new_key_id(self) -> str:
        while True:
            key_id = f"lore_key_{uuid4().hex[:12]}"
            if self.get_key(key_id) is None:
                return key_id

    @staticmethod
    def _row_to_key(row: sqlite3.Row) -> LoreApiKey:
        role = str(row["role"] or "writer")
        if role not in VALID_API_KEY_ROLES:
            role = "writer"
        return LoreApiKey(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            role=role,
            key_prefix=str(row["key_prefix"] or ""),
            created_at=str(row["created_at"]),
            revoked_at=str(row["revoked_at"]) if row["revoked_at"] else None,
        )
