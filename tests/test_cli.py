from __future__ import annotations

import json

from lore_app.cli import main


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
    assert main(["backup", "--content-dir", str(source), "--output", str(backup_file)]) == 0
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
