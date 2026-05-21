from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ensure-consolidation-key.py"
SPEC = importlib.util.spec_from_file_location("ensure_consolidation_key_script", SCRIPT_PATH)
assert SPEC is not None
ensure_script = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = ensure_script
SPEC.loader.exec_module(ensure_script)


def test_ensure_consolidation_key_creates_and_preserves_valid_token(tmp_path):
    env_file = tmp_path / "lore-consolidation.env"
    db_file = tmp_path / "api_keys.db"
    env_file.write_text("LORE_URL=http://127.0.0.1:8210\nLORE_BEARER_TOKEN=\n", encoding="utf-8")

    created = ensure_script.ensure_consolidation_key(env_file, db_file, "8078")
    token = _read_env_value(env_file, "LORE_BEARER_TOKEN")

    assert created.token_status == "created"
    assert created.lore_url == "http://127.0.0.1:8078"
    assert token.startswith("lore_")
    assert _token_row(db_file, token)["role"] == "writer"

    preserved = ensure_script.ensure_consolidation_key(env_file, db_file, "8078")

    assert preserved.token_status == "preserved"
    assert _read_env_value(env_file, "LORE_BEARER_TOKEN") == token


def test_ensure_consolidation_key_replaces_revoked_token(tmp_path):
    env_file = tmp_path / "lore-consolidation.env"
    db_file = tmp_path / "api_keys.db"

    ensure_script.ensure_consolidation_key(env_file, db_file, "8078")
    revoked_token = _read_env_value(env_file, "LORE_BEARER_TOKEN")

    with sqlite3.connect(db_file) as connection:
        connection.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE key_hash = ?",
            ("2026-05-17T00:00:00+00:00", ensure_script.hash_api_key(revoked_token)),
        )
        connection.commit()

    replaced = ensure_script.ensure_consolidation_key(env_file, db_file, "8078")
    new_token = _read_env_value(env_file, "LORE_BEARER_TOKEN")

    assert replaced.token_status == "replaced"
    assert new_token.startswith("lore_")
    assert new_token != revoked_token
    assert _token_row(db_file, new_token)["revoked_at"] is None


def test_ensure_consolidation_key_replaces_reader_token(tmp_path):
    env_file = tmp_path / "lore-consolidation.env"
    db_file = tmp_path / "api_keys.db"

    ensure_script.ensure_consolidation_key(env_file, db_file, "8078")
    reader_token = _read_env_value(env_file, "LORE_BEARER_TOKEN")

    with sqlite3.connect(db_file) as connection:
        connection.execute(
            "UPDATE api_keys SET role = ? WHERE key_hash = ?",
            ("reader", ensure_script.hash_api_key(reader_token)),
        )
        connection.commit()

    replaced = ensure_script.ensure_consolidation_key(env_file, db_file, "8078")
    new_token = _read_env_value(env_file, "LORE_BEARER_TOKEN")

    assert replaced.token_status == "replaced"
    assert new_token != reader_token
    assert _token_row(db_file, new_token)["role"] == "writer"


def _read_env_value(env_file: Path, key: str) -> str:
    lines = env_file.read_text(encoding="utf-8").splitlines()
    return ensure_script.get_env_value(lines, key)


def _token_row(db_file: Path, token: str) -> sqlite3.Row:
    connection = sqlite3.connect(db_file)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT name, role, key_prefix, revoked_at FROM api_keys WHERE key_hash = ?",
            (ensure_script.hash_api_key(token),),
        ).fetchone()
        assert row is not None
        return row
    finally:
        connection.close()
