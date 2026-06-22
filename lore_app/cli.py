"""Lore CLI - vault management from the command line."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-admin", description="Lore operator/admin CLI")
    sub = parser.add_subparsers(dest="command")

    p_bootstrap = sub.add_parser("bootstrap", help="Initialize a new Lore vault")
    p_bootstrap.add_argument("path", help="Directory to initialize")

    p_backup = sub.add_parser("backup", help="Export vault to a tar.gz backup")
    p_backup.add_argument("--content-dir", default="./data/pages", help="Content directory")
    p_backup.add_argument("--output", required=True, help="Output tar.gz file")
    p_backup.add_argument("--ledger-db", default="./data/db/ledger.db", help="Claim ledger database")
    p_backup.add_argument("--settings-db", default="./data/db/settings.db", help="Settings database")
    p_backup.add_argument("--api-keys-db", default="./data/db/api_keys.db", help="API keys database")

    p_restore = sub.add_parser("restore", help="Restore vault from a tar.gz backup")
    p_restore.add_argument("--content-dir", default="./data/pages", help="Content directory")
    p_restore.add_argument("--search-db", default="./data/search.db", help="Search index database")
    p_restore.add_argument("--input", required=True, help="Input tar.gz file")
    p_restore.add_argument("--ledger-db", default="./data/db/ledger.db", help="Claim ledger database")
    p_restore.add_argument("--vector-db", default="./data/db/vectors.db", help="Vector index database")
    p_restore.add_argument("--settings-db", default="./data/db/settings.db", help="Settings database")
    p_restore.add_argument("--api-keys-db", default="./data/db/api_keys.db", help="API keys database")

    p_export = sub.add_parser("export", help="Export vault pages")
    p_export.add_argument("--content-dir", default="./data/pages", help="Content directory")
    p_export.add_argument("--output", default="-", help="Output file (- for stdout)")
    p_export.add_argument("--format", choices=["json", "markdown"], default="json", help="Export format")

    p_import = sub.add_parser("import", help="Import pages from JSON")
    p_import.add_argument("input", nargs="?", help="Input JSON file")
    p_import.add_argument("--input", dest="input_option", help="Input JSON file")
    p_import.add_argument("--content-dir", default="./data/pages", help="Content directory")

    p_verify = sub.add_parser("verify", help="Verify backup integrity")
    p_verify.add_argument("--input", required=True, help="Input tar.gz file")

    p_consolidate = sub.add_parser("consolidate", help="Run one consolidation pass")
    p_consolidate.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run (default: True, no changes applied)",
    )
    p_consolidate.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Enable auto-apply of safe plans (implies --no-dry-run)",
    )
    p_consolidate.add_argument(
        "--max-auto-apply",
        type=int,
        default=0,
        help="Maximum plans to auto-apply (default: 0)",
    )
    p_consolidate.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Capture batch size (default: 10)",
    )
    p_consolidate.add_argument(
        "--force-reextract",
        action="store_true",
        default=False,
        help="Force re-extraction of already-extracted captures",
    )

    p_extraction = sub.add_parser("extraction", help="Extraction maintenance commands")
    extraction_sub = p_extraction.add_subparsers(dest="extraction_command")
    p_extraction_retry = extraction_sub.add_parser("retry", help="Retry unresolved extraction dead-letters")
    p_extraction_retry.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum unresolved dead-letters to retry (default: 10)",
    )

    sub.add_parser("status", help="Show consolidation status")

    sub.add_parser("info", help="Show vault info")

    p_key = sub.add_parser("key", help="Manage Lore agent API keys")
    key_sub = p_key.add_subparsers(dest="key_command")
    p_key_create = key_sub.add_parser("create", help="Create an API key and print its token once")
    p_key_create.add_argument("--name", required=True, help="Human-readable key name")
    p_key_create.add_argument(
        "--role", default="admin", choices=["admin", "writer", "reader"], help="Key role (default: admin)"
    )
    p_key_create.add_argument("--description", default="", help="Optional description")

    args = parser.parse_args(argv)

    if args.command == "bootstrap":
        return cmd_bootstrap(args.path)
    if args.command == "backup":
        return cmd_backup(
            args.content_dir,
            args.output,
            ledger_db=args.ledger_db,
            settings_db=args.settings_db,
            api_keys_db=args.api_keys_db,
        )
    if args.command == "restore":
        return cmd_restore(
            args.input,
            args.content_dir,
            args.search_db,
            ledger_db=args.ledger_db,
            vector_db=args.vector_db,
            settings_db=args.settings_db,
            api_keys_db=args.api_keys_db,
        )
    if args.command == "export":
        return cmd_export(args.content_dir, args.output, args.format)
    if args.command == "import":
        input_file = args.input_option or args.input
        if not input_file:
            parser.error("import requires an input file")
        return cmd_import(input_file, args.content_dir)
    if args.command == "verify":
        return cmd_verify(args.input)
    if args.command == "consolidate":
        return cmd_consolidate(args)
    if args.command == "extraction":
        if args.extraction_command == "retry":
            return cmd_extraction_retry(args)
        parser.error("extraction requires a subcommand")
    if args.command == "status":
        return cmd_status(args)
    if args.command == "info":
        return cmd_info()
    if args.command == "key":
        if args.key_command == "create":
            return cmd_key_create(args)
        parser.error("key requires a subcommand")

    parser.print_help()
    return 0


def cmd_key_create(args: argparse.Namespace) -> int:
    """Bootstrap an agent API key directly (no running server / proxy session needed)."""
    from .api_keys import LoreApiKeyStore
    from .config import LoreConfig

    config = LoreConfig()
    store = LoreApiKeyStore(config.api_keys_db)
    store.initialize()
    try:
        key, raw_key = store.create_key(name=args.name, description=args.description, role=args.role)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        store.close()
    print(f"Created API key '{key.name}' (role={key.role}, id={key.id}).")
    print("Token (shown once — store it securely and send it as 'Authorization: Bearer <token>'):")
    print(raw_key)
    return 0


def cmd_bootstrap(path: str) -> int:
    vault = Path(path)
    pages = vault / "pages"
    pages.mkdir(parents=True, exist_ok=True)

    welcome = pages / "welcome.md"
    if not welcome.exists():
        welcome.write_text(
            "---\ntitle: Welcome to Lore\nkind: page\nvisibility: public\n---\n\n# Welcome to Lore\n\nThis is your new knowledge vault.\n",
            encoding="utf-8",
        )

    print(f"Bootstrapped Lore vault at {vault}")
    return 0


def cmd_backup(
    content_dir: str,
    output: str,
    *,
    ledger_db: str | None = None,
    settings_db: str | None = None,
    api_keys_db: str | None = None,
) -> int:
    from .repository import LoreRepository

    repo = LoreRepository(content_dir)
    pages = []
    checksums = {}
    for summary in repo.list_pages():
        page = repo.read_page(summary.id)
        if page is None:
            continue
        pages.append(page)
        checksums[page.id] = sha256_text(page.content)

    # Source DBs to snapshot. The claim ledger holds non-reconstructable derived
    # state (claim strengths, access/recency/salience signals, traces, patch-plan
    # history, bi-temporal validity); omitting it makes a restore reset recall.
    db_sources = {
        "ledger.db": ledger_db,
        "settings.db": settings_db,
        "api_keys.db": api_keys_db,
    }

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        pages_dir = root / "pages"
        pages_dir.mkdir()
        db_dir = root / "db"
        db_dir.mkdir()

        included_dbs: list[str] = []
        for name, src in db_sources.items():
            if src and backup_sqlite_db(src, db_dir / name):
                included_dbs.append(name)

        manifest = {
            "version": 2,
            "timestamp": datetime.now(UTC).isoformat(),
            "page_count": len(pages),
            "checksums": checksums,
            "databases": included_dbs,
        }
        metadata = {
            "catalog": repo.catalog().model_dump(),
            "pages": [page.model_dump(exclude={"content", "body"}) for page in pages],
        }

        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (root / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        for page in pages:
            (pages_dir / backup_page_filename(page.id)).write_text(page.content, encoding="utf-8", newline="\n")
        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(root / "manifest.json", arcname="manifest.json")
            archive.add(root / "metadata.json", arcname="metadata.json")
            for page_file in sorted(pages_dir.glob("*.md")):
                archive.add(page_file, arcname=f"pages/{page_file.name}")
            for name in included_dbs:
                archive.add(db_dir / name, arcname=f"db/{name}")

    db_note = f", {len(included_dbs)} database(s)" if included_dbs else ""
    print(f"Backed up {len(pages)} pages{db_note} to {output}")
    return 0


def backup_sqlite_db(src_path: str, dest_path: Path) -> bool:
    """Snapshot a SQLite DB to dest via the online backup API.

    Uses ``sqlite3.Connection.backup`` so the copy is a consistent snapshot even
    under WAL/concurrent writes (the raw on-disk file can miss un-checkpointed WAL
    frames). Returns False when the source does not exist.
    """
    import sqlite3

    if not Path(src_path).exists():
        return False
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(str(dest_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return True


def cmd_restore(
    input_file: str,
    content_dir: str,
    search_db: str,
    *,
    ledger_db: str | None = None,
    vector_db: str | None = None,
    settings_db: str | None = None,
    api_keys_db: str | None = None,
) -> int:
    from .repository import InvalidPageId, LoreRepository
    from .search_index import LoreSearchIndex

    backup = read_backup(input_file)
    repo = LoreRepository(content_dir)
    restored = 0
    for page_id, content in backup["pages"].items():
        try:
            repo.upsert_page(page_id, content)
            restored += 1
        except InvalidPageId:
            print(f"Skipped invalid page: {page_id}")

    indexed = LoreSearchIndex(search_db).rebuild(repo)

    # Restore the snapshotted databases (ledger/settings/api_keys) by writing the
    # archived bytes into place; tighten perms on the secret-bearing ones.
    db_targets = {
        "ledger.db": ledger_db,
        "settings.db": settings_db,
        "api_keys.db": api_keys_db,
    }
    restored_dbs: list[str] = []
    for name, data in (backup.get("databases") or {}).items():
        target = db_targets.get(name)
        if not target:
            continue
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)
        if name in ("ledger.db", "api_keys.db"):
            # chmod is a POSIX no-op concept on some filesystems; best-effort.
            with contextlib.suppress(OSError):
                target_path.chmod(0o600)
        restored_dbs.append(name)

    # Rebuild the vector index from the restored pages (the index is not archived).
    vector_indexed = 0
    if vector_db:
        from .rag.vector_store import VectorStore
        from .route_utils import rebuild_vector_index

        vector_store = VectorStore(vector_db)
        try:
            vector_indexed = rebuild_vector_index(repo, vector_store)
        finally:
            vector_store.close()

    print(f"Restored {restored} pages from {input_file}")
    print(f"Rebuilt search index with {indexed} pages")
    print(f"Rebuilt vector index with {vector_indexed} pages")
    if restored_dbs:
        print(f"Restored database(s): {', '.join(restored_dbs)}")
    return 0


def cmd_export(content_dir: str, output: str, export_format: str = "json") -> int:
    from .repository import LoreRepository

    repo = LoreRepository(content_dir)
    pages = []
    for summary in repo.list_pages():
        page = repo.read_page(summary.id)
        if page:
            pages.append(page.model_dump())

    if export_format == "markdown":
        data = "\n\n".join(f"<!-- lore-page-id: {page['id']} -->\n\n{page['content'].rstrip()}" for page in pages)
    else:
        data = json.dumps({"pages": pages}, indent=2, ensure_ascii=False)
    if output == "-":
        sys.stdout.write(data)
    else:
        Path(output).write_text(data, encoding="utf-8")
        print(f"Exported {len(pages)} pages to {output}")
    return 0


def cmd_import(input_file: str, content_dir: str) -> int:
    from .repository import InvalidPageId, LoreRepository

    repo = LoreRepository(content_dir)
    data = json.loads(Path(input_file).read_text(encoding="utf-8"))
    count = 0
    for page_data in data.get("pages", []):
        page_id = page_data.get("id", "")
        content = page_data.get("content", "")
        if page_id and content:
            try:
                repo.upsert_page(page_id, content)
                count += 1
            except InvalidPageId:
                print(f"Skipped invalid page: {page_id}")
    print(f"Imported {count} pages")
    return 0


def cmd_verify(input_file: str) -> int:
    backup = read_backup(input_file)
    manifest = backup["manifest"]
    page_count = int(manifest.get("page_count") or 0)
    actual_count = len(backup["pages"])
    if page_count != actual_count:
        print(f"Backup invalid: manifest has {page_count} pages, archive has {actual_count}")
        return 1
    databases = backup.get("databases") or {}
    if int(manifest.get("version") or 1) >= 2:
        print(
            f"Backup verified: {actual_count} pages, {len(databases)} database(s), "
            f"version {manifest.get('version')}"
        )
    else:
        print(f"Backup verified: {actual_count} pages, version {manifest.get('version')}")
    return 0


def cmd_info() -> int:
    from .config import LoreConfig

    config = LoreConfig()
    for key, value in config.to_dict().items():
        print(f"  {key}: {value}")
    return 0


def cmd_consolidate(args: argparse.Namespace) -> int:
    from .audit import AuditLog
    from .config import LoreConfig, merged_llm_config
    from .consolidation_worker import ConsolidationWorker
    from .ledger import LedgerDB
    from .llm_provider import build_llm_client_from_config
    from .patch_planner import PatchPlanner
    from .policy_engine import PolicyEngine
    from .repository import LoreRepository
    from .settings_store import SettingsStore

    config = LoreConfig()
    dry_run = not args.apply
    max_auto_apply = args.max_auto_apply if args.max_auto_apply else (5 if args.apply else 0)

    repo = LoreRepository(config.content_dir)
    repo.ensure_root()
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    audit = AuditLog(
        Path(config.content_dir) / ".lore" / "audit",
        retention_days=config.audit_retention_days,
    )
    planner = PatchPlanner(repo, ledger, audit, policy_engine=PolicyEngine(ledger))
    worker = ConsolidationWorker(repo, ledger, planner, config, audit)

    # The scheduled runner has no app.state, so build the configured LLM client
    # from runtime settings here and hand it to the run.
    settings_store = SettingsStore(config.settings_db)
    settings_store.initialize()
    llm_client = build_llm_client_from_config(merged_llm_config(config, settings_store))

    try:
        result = worker.run(
            dry_run=dry_run,
            batch_size=args.batch_size,
            max_auto_apply=max_auto_apply,
            force_reextract=args.force_reextract,
            llm_client=llm_client,
        )
    finally:
        close = getattr(llm_client, "close", None)
        if callable(close):
            close()

    print(json.dumps(result.model_dump(), indent=2, default=str))
    return 1 if result.errors else 0


def cmd_status(args: argparse.Namespace) -> int:
    from .config import LoreConfig
    from .ledger import LedgerDB
    from .repository import LoreRepository

    config = LoreConfig()
    repo = LoreRepository(config.content_dir)
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    ledger_status = ledger.get_consolidation_status()

    last_run = ledger_status.get("last_run")
    plans_by_status = {
        "draft": 0,
        "pending": 0,
        "needs_manual_review": 0,
        "applied": 0,
        "rejected": 0,
        **ledger_status.get("plans_by_status", {}),
    }
    pending_captures = sum(
        1
        for page in repo.list_pages(kind="capture")
        if page.status == "draft"
        and page.id.startswith(("inbox/", "notes/"))
        and not ledger.is_capture_extracted(page.id)
    )
    last_run_data = last_run if last_run else {}
    errors = last_run_data.get("errors")
    status = {
        "last_run": last_run_data.get("completed_at") or last_run_data.get("started_at"),
        "pending_captures": pending_captures,
        "plans_by_status": plans_by_status,
        "generated_plans": sum(plans_by_status.values()),
        "auto_applied": plans_by_status.get("applied", 0),
        "review_required": plans_by_status.get("needs_manual_review", 0) + plans_by_status.get("pending", 0),
        "errors": errors if isinstance(errors, list) else [],
        "stuck_runs": len(ledger_status.get("stuck_runs", [])),
    }
    print(json.dumps(status, indent=2, default=str))
    return 0


def cmd_extraction_retry(args: argparse.Namespace) -> int:
    from .config import LoreConfig
    from .extraction import retry_deadletter
    from .ledger import LedgerDB
    from .llm_provider import build_llm_client
    from .repository import LoreRepository

    config = LoreConfig()
    repo = LoreRepository(config.content_dir)
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    llm_client = build_llm_client(config=config)

    retried = 0
    resolved = 0
    deadletters = ledger.list_deadletters(status="unresolved", limit=args.limit)
    for deadletter in deadletters:
        result = retry_deadletter(
            repo,
            str(deadletter["deadletter_id"]),
            ledger_db=ledger,
            llm_client=llm_client,
        )
        if result is None:
            continue
        if result.retried:
            retried += 1
        if result.resolved:
            resolved += 1

    print(json.dumps({"retried": retried, "resolved": resolved}, indent=2))
    return 0


def read_backup(input_file: str) -> dict[str, Any]:
    from .repository import normalize_page_id

    with tarfile.open(input_file, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        if "manifest.json" not in members:
            raise ValueError("Backup missing manifest.json")
        manifest_file = archive.extractfile(members["manifest.json"])
        if manifest_file is None:
            raise ValueError("Backup manifest is unreadable")
        manifest = json.loads(manifest_file.read().decode("utf-8"))
        if manifest.get("version") not in (1, 2):
            raise ValueError(f"Unsupported backup version: {manifest.get('version')}")

        checksums = manifest.get("checksums") or {}
        if not isinstance(checksums, dict):
            raise ValueError("Backup manifest checksums must be an object")

        pages: dict[str, str] = {}
        for page_id, checksum in checksums.items():
            normalized = normalize_page_id(page_id)
            member_name = f"pages/{backup_page_filename(normalized)}"
            if member_name not in members:
                raise ValueError(f"Backup missing page file for {normalized}")
            page_file = archive.extractfile(members[member_name])
            if page_file is None:
                raise ValueError(f"Backup page file is unreadable: {normalized}")
            content = page_file.read().decode("utf-8")
            actual = sha256_text(content)
            if actual != checksum:
                raise ValueError(f"Checksum mismatch for {normalized}")
            pages[normalized] = content

        # v2 backups carry binary SQLite snapshots under db/<name>. Tar integrity
        # covers them (they are binary, so not text-checksummed). v1 has none.
        databases: dict[str, bytes] = {}
        if int(manifest.get("version") or 1) >= 2:
            for name in manifest.get("databases") or []:
                member_name = f"db/{name}"
                if member_name not in members:
                    raise ValueError(f"Backup missing database file for {name}")
                db_file = archive.extractfile(members[member_name])
                if db_file is None:
                    raise ValueError(f"Backup database file is unreadable: {name}")
                databases[name] = db_file.read()

    return {"manifest": manifest, "pages": pages, "databases": databases}


def backup_page_filename(page_id: str) -> str:
    return f"{page_id.replace('/', '__')}.md"


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
