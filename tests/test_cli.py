from __future__ import annotations

import json

from lore_app.cli import main
from lore_app.ledger import LedgerDB
from lore_app.schemas import ContextRef, ExtractedClaim, ExtractionResult, TraceEntry


def _write_source_page(source):
    source.mkdir(parents=True, exist_ok=True)
    (source / "projects").mkdir()
    (source / "projects" / "example-project.md").write_text(
        "---\ntitle: ExampleProject\nkind: project\nvisibility: internal\n---\n\n# ExampleProject\n",
        encoding="utf-8",
    )


def _seed_ledger(path):
    ledger = LedgerDB(path)
    ledger.initialize()
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-backup",
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-10/backup"],
            claims=[
                ExtractedClaim(
                    subject="services/backup",
                    predicate="states",
                    object="The ledger holds non-reconstructable derived state.",
                    confidence="high",
                ),
                ExtractedClaim(
                    subject="services/restore",
                    predicate="states",
                    object="Restore must preserve claim strengths and ranking.",
                    confidence="medium",
                ),
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    # Differentiate the two claims so recall ranking is non-trivial.
    claims = ledger.get_active_claims()
    ledger.record_claim_access([claims[0]["candidate_id"]], now="2026-05-12T00:00:00+00:00", actor=None)
    ledger.apply_decay(days_since_access=20)
    ledger.store_trace(
        TraceEntry(
            trace_id="",
            actor="tester",
            reason_summary="Backup round-trip trace.",
            context_refs=[ContextRef(type="page", id="services/backup")],
        )
    )
    return ledger


def test_cli_bootstrap_export_import(tmp_path, capsys):
    vault = tmp_path / "vault"
    assert main(["bootstrap", str(vault)]) == 0
    assert (vault / "pages" / "welcome.md").is_file()

    export_file = tmp_path / "export.json"
    assert main(["export", "--content-dir", str(vault / "pages"), "--output", str(export_file)]) == 0
    payload = json.loads(export_file.read_text(encoding="utf-8"))
    assert payload["pages"][0]["id"] == "welcome"

    imported = tmp_path / "imported"
    assert main(["import", str(export_file), "--content-dir", str(imported)]) == 0
    assert (imported / "welcome.md").is_file()

    output = capsys.readouterr().out
    assert "Bootstrapped Lore vault" in output
    assert "Exported 1 pages" in output
    assert "Imported 1 pages" in output


def test_cli_backup_verify_restore(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    (source / "projects").mkdir()
    (source / "projects" / "example-project.md").write_text(
        "---\ntitle: ExampleProject\nkind: project\nvisibility: internal\n---\n\n# ExampleProject\n",
        encoding="utf-8",
    )

    backup_file = tmp_path / "lore-backup.tar.gz"
    assert (
        main(
            [
                "backup",
                "--content-dir",
                str(source),
                "--output",
                str(backup_file),
                "--ledger-db",
                str(tmp_path / "src-ledger.db"),
                "--settings-db",
                str(tmp_path / "src-settings.db"),
                "--api-keys-db",
                str(tmp_path / "src-api-keys.db"),
            ]
        )
        == 0
    )
    assert backup_file.is_file()

    assert main(["verify", "--input", str(backup_file)]) == 0

    restored = tmp_path / "restored"
    search_db = tmp_path / "restored-search.db"
    assert (
        main(
            [
                "restore",
                "--input",
                str(backup_file),
                "--content-dir",
                str(restored),
                "--search-db",
                str(search_db),
                "--ledger-db",
                str(tmp_path / "restored-ledger.db"),
                "--vector-db",
                str(tmp_path / "restored-vectors.db"),
                "--settings-db",
                str(tmp_path / "restored-settings.db"),
                "--api-keys-db",
                str(tmp_path / "restored-api-keys.db"),
            ]
        )
        == 0
    )
    assert (restored / "projects" / "example-project.md").is_file()
    assert search_db.is_file()

    output = capsys.readouterr().out
    assert "Backed up 1 pages" in output
    assert "Backup verified: 1 pages" in output
    assert "Restored 1 pages" in output


def test_cli_backup_restore_preserves_ledger_strengths_and_ranking(tmp_path):
    source = tmp_path / "source"
    _write_source_page(source)
    src_ledger = tmp_path / "src-ledger.db"
    ledger = _seed_ledger(src_ledger)

    before_claims = {c["candidate_id"]: c for c in ledger.get_active_claims()}
    before_order = [row["candidate_id"] for row in ledger.recall_claims(query=None, limit=50)]
    before_traces = ledger.count_traces()
    ledger.close()
    assert before_traces >= 1

    backup_file = tmp_path / "backup.tar.gz"
    assert (
        main(
            [
                "backup",
                "--content-dir",
                str(source),
                "--output",
                str(backup_file),
                "--ledger-db",
                str(src_ledger),
                "--settings-db",
                str(tmp_path / "src-settings.db"),
                "--api-keys-db",
                str(tmp_path / "src-api-keys.db"),
            ]
        )
        == 0
    )

    restored_ledger = tmp_path / "restored-ledger.db"
    assert (
        main(
            [
                "restore",
                "--input",
                str(backup_file),
                "--content-dir",
                str(tmp_path / "restored"),
                "--search-db",
                str(tmp_path / "restored-search.db"),
                "--ledger-db",
                str(restored_ledger),
                "--vector-db",
                str(tmp_path / "restored-vectors.db"),
                "--settings-db",
                str(tmp_path / "restored-settings.db"),
                "--api-keys-db",
                str(tmp_path / "restored-api-keys.db"),
            ]
        )
        == 0
    )

    restored = LedgerDB(restored_ledger)
    restored.initialize()
    after_claims = {c["candidate_id"]: c for c in restored.get_active_claims()}
    after_order = [row["candidate_id"] for row in restored.recall_claims(query=None, limit=50)]

    # Binary-snapshot restore must preserve strengths, access signals, ranking, traces.
    assert after_order == before_order
    assert set(after_claims) == set(before_claims)
    for cid, before in before_claims.items():
        assert after_claims[cid]["strength"] == before["strength"]
        assert after_claims[cid]["access_count"] == before["access_count"]
    assert restored.count_traces() == before_traces
    restored.close()


def test_cli_restore_rebuilds_vector_index(tmp_path):
    source = tmp_path / "source"
    _write_source_page(source)

    backup_file = tmp_path / "backup.tar.gz"
    assert (
        main(
            [
                "backup",
                "--content-dir",
                str(source),
                "--output",
                str(backup_file),
                "--ledger-db",
                str(tmp_path / "none-ledger.db"),
                "--settings-db",
                str(tmp_path / "none-settings.db"),
                "--api-keys-db",
                str(tmp_path / "none-api-keys.db"),
            ]
        )
        == 0
    )

    vector_db = tmp_path / "restored-vectors.db"
    assert (
        main(
            [
                "restore",
                "--input",
                str(backup_file),
                "--content-dir",
                str(tmp_path / "restored"),
                "--search-db",
                str(tmp_path / "restored-search.db"),
                "--vector-db",
                str(vector_db),
                "--ledger-db",
                str(tmp_path / "restored-ledger.db"),
                "--settings-db",
                str(tmp_path / "restored-settings.db"),
                "--api-keys-db",
                str(tmp_path / "restored-api-keys.db"),
            ]
        )
        == 0
    )

    from lore_app.rag.vector_store import VectorStore

    assert vector_db.is_file()
    store = VectorStore(vector_db)
    try:
        assert store.chunk_count() > 0
    finally:
        store.close()


def test_cli_verify_accepts_v2_backup_with_databases(tmp_path, capsys):
    source = tmp_path / "source"
    _write_source_page(source)
    src_ledger = tmp_path / "src-ledger.db"
    _seed_ledger(src_ledger).close()

    backup_file = tmp_path / "backup.tar.gz"
    assert (
        main(
            [
                "backup",
                "--content-dir",
                str(source),
                "--output",
                str(backup_file),
                "--ledger-db",
                str(src_ledger),
                "--settings-db",
                str(tmp_path / "none-settings.db"),
                "--api-keys-db",
                str(tmp_path / "none-api-keys.db"),
            ]
        )
        == 0
    )

    assert main(["verify", "--input", str(backup_file)]) == 0
    output = capsys.readouterr().out
    assert "version 2" in output
    assert "1 database(s)" in output


def test_cli_info(capsys):
    assert main(["info"]) == 0
    output = capsys.readouterr().out
    assert "app_name:" in output
    assert "content_dir:" in output


def test_cli_key_create_mints_admin_token(tmp_path, monkeypatch, capsys):
    from lore_app.api_keys import LoreApiKeyStore
    from lore_app.cli import main

    db = tmp_path / "api_keys.db"
    monkeypatch.setenv("LORE_API_KEYS_DB", str(db))
    rc = main(["key", "create", "--name", "codex", "--role", "admin"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lore_" in out  # the token is printed once
    # The key is persisted and verifiable.
    store = LoreApiKeyStore(db)
    store.initialize()
    token = next(
        line.strip() for line in out.splitlines() if line.strip().startswith("lore_") and " " not in line.strip()
    )
    verified = store.verify_token(token)
    assert verified is not None and verified.role == "admin"
    store.close()
