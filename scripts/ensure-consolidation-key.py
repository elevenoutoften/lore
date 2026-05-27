#!/usr/bin/env python3
"""Ensure the Lore consolidation timer has a valid Lore API key."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

KEY_NAME = "axis-lore-consolidation"
KEY_DESCRIPTION = "Systemd timer for Lore consolidation runs."
KEY_ROLE = "writer"


@dataclass(frozen=True)
class EnsureResult:
    env_file: Path
    api_keys_db: Path
    lore_url: str
    token_status: str
    key_prefix: str

    def to_safe_json(self) -> str:
        return json.dumps(
            {
                "env_file": str(self.env_file),
                "api_keys_db": str(self.api_keys_db),
                "lore_url": self.lore_url,
                "token_status": self.token_status,
                "key_prefix": self.key_prefix,
            },
            sort_keys=True,
        )


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key_secret() -> str:
    return "lore_" + secrets.token_urlsafe(32)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
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
    connection.commit()


def token_is_valid(connection: sqlite3.Connection, token: str) -> bool:
    if not token:
        return False
    row = connection.execute(
        """
        SELECT role
        FROM api_keys
        WHERE key_hash = ? AND revoked_at IS NULL
        LIMIT 1
        """,
        (hash_api_key(token),),
    ).fetchone()
    if row is None:
        return False
    return str(row[0] or "") in {"admin", "writer"}


def create_key(connection: sqlite3.Connection) -> str:
    token = generate_api_key_secret()
    now = utc_now()
    while True:
        key_id = f"lore_key_{uuid4().hex[:12]}"
        existing = connection.execute(
            "SELECT 1 FROM api_keys WHERE id = ? LIMIT 1",
            (key_id,),
        ).fetchone()
        if existing is None:
            break

    connection.execute(
        """
        INSERT INTO api_keys (
            id, name, description, role, key_prefix, key_hash, created_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            key_id,
            KEY_NAME,
            KEY_DESCRIPTION,
            KEY_ROLE,
            token[:16],
            hash_api_key(token),
            now,
        ),
    )
    connection.commit()
    return token


def read_env_lines(env_file: Path) -> list[str]:
    if not env_file.exists():
        return []
    return env_file.read_text(encoding="utf-8").splitlines()


def get_env_value(lines: list[str], key: str) -> str:
    prefix = f"{key}="
    export_prefix = f"export {key}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip().strip('"').strip("'")
        if stripped.startswith(export_prefix):
            return stripped[len(export_prefix) :].strip().strip('"').strip("'")
    return ""


def set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    prefixes = (f"{key}=", f"export {key}=")
    updated: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefixes):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    return updated


def write_env_lines(env_file: Path, lines: list[str]) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def ensure_consolidation_key(env_file: Path, api_keys_db: Path, lore_port: str) -> EnsureResult:
    lore_url = f"http://127.0.0.1:{lore_port}"
    api_keys_db.parent.mkdir(parents=True, exist_ok=True)
    lines = read_env_lines(env_file)
    token = get_env_value(lines, "LORE_BEARER_TOKEN")
    had_token = bool(token)

    with sqlite3.connect(api_keys_db) as connection:
        ensure_schema(connection)
        if token_is_valid(connection, token):
            status = "preserved"
        else:
            token = create_key(connection)
            status = "replaced" if had_token else "created"

    lines = set_env_value(lines, "LORE_URL", lore_url)
    lines = set_env_value(lines, "LORE_BEARER_TOKEN", token)
    write_env_lines(env_file, lines)

    return EnsureResult(
        env_file=env_file,
        api_keys_db=api_keys_db,
        lore_url=lore_url,
        token_status=status,
        key_prefix=token[:16],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure /etc/axis/lore-consolidation.env has a valid Lore API key.",
    )
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--api-keys-db", required=True, type=Path)
    parser.add_argument("--lore-port", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = ensure_consolidation_key(args.env_file, args.api_keys_db, args.lore_port)
    print(result.to_safe_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
