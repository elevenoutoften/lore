"""Lore CLI - vault management from the command line."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore", description="Lore knowledge wiki CLI")
    sub = parser.add_subparsers(dest="command")

    p_bootstrap = sub.add_parser("bootstrap", help="Initialize a new Lore vault")
    p_bootstrap.add_argument("path", help="Directory to initialize")

    p_backup = sub.add_parser("backup", help="Export vault to a tar.gz backup")
    p_backup.add_argument("--content-dir", default="./data/pages", help="Content directory")
    p_backup.add_argument("--output", required=True, help="Output tar.gz file")

    p_restore = sub.add_parser("restore", help="Restore vault from a tar.gz backup")
    p_restore.add_argument("--content-dir", default="./data/pages", help="Content directory")
    p_restore.add_argument("--search-db", default="./data/search.db", help="Search index database")
    p_restore.add_argument("--input", required=True, help="Input tar.gz file")

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

    args = parser.parse_args(argv)

    if args.command == "bootstrap":
        return cmd_bootstrap(args.path)
    if args.command == "backup":
        return cmd_backup(args.content_dir, args.output)
    if args.command == "restore":
        return cmd_restore(args.input, args.content_dir, args.search_db)
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

    parser.print_help()
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


def cmd_backup(content_dir: str, output: str) -> int:
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

    manifest = {
        "version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "page_count": len(pages),
        "checksums": checksums,
    }
    metadata = {
        "catalog": repo.catalog().model_dump(),
        "pages": [page.model_dump(exclude={"content", "body"}) for page in pages],
    }

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        pages_dir = root / "pages"
        pages_dir.mkdir()
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (root / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        for page in pages:
            (pages_dir / backup_page_filename(page.id)).write_text(page.content, encoding="utf-8", newline="\n")
        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(root / "manifest.json", arcname="manifest.json")
            archive.add(root / "metadata.json", arcname="metadata.json")
            for page_file in sorted(pages_dir.glob("*.md")):
                archive.add(page_file, arcname=f"pages/{page_file.name}")

    print(f"Backed up {len(pages)} pages to {output}")
    return 0


def cmd_restore(input_file: str, content_dir: str, search_db: str) -> int:
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
    print(f"Restored {restored} pages from {input_file}")
    print(f"Rebuilt search index with {indexed} pages")
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
    from .config import LoreConfig
    from .consolidation_worker import ConsolidationWorker
    from .ledger import LedgerDB
    from .patch_planner import PatchPlanner
    from .policy_engine import PolicyEngine
    from .repository import LoreRepository

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

    result = worker.run(
        dry_run=dry_run,
        batch_size=args.batch_size,
        max_auto_apply=max_auto_apply,
        force_reextract=args.force_reextract,
    )

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
        "review": 0,
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
        "review_required": plans_by_status.get("review", 0) + plans_by_status.get("pending", 0),
        "errors": errors if isinstance(errors, list) else [],
        "stuck_runs": len(ledger_status.get("stuck_runs", [])),
    }
    print(json.dumps(status, indent=2, default=str))
    return 0


def cmd_extraction_retry(args: argparse.Namespace) -> int:
    from .config import LoreConfig
    from .extraction import extract_from_captures
    from .ledger import LedgerDB
    from .llm_provider import NoLlmClient, build_llm_client
    from .repository import LoreRepository

    config = LoreConfig()
    repo = LoreRepository(config.content_dir)
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    llm_client = build_llm_client(config=config)
    resolved_by = "deterministic" if isinstance(llm_client, NoLlmClient) else "llm"

    retried = 0
    resolved = 0
    deadletters = ledger.list_deadletters(status="unresolved", limit=args.limit)
    for deadletter in deadletters:
        retried += 1
        ledger.increment_retry(str(deadletter["deadletter_id"]))
        capture_id = str(deadletter["capture_id"])
        ledger.reset_extraction(capture_ids=[capture_id])
        try:
            result = extract_from_captures(
                repo,
                capture_ids=[capture_id],
                dry_run=False,
                ledger_db=ledger,
                llm_client=llm_client,
            )
        except Exception:
            continue
        if result.source_capture_ids and ledger.resolve_deadletter(
            str(deadletter["deadletter_id"]),
            resolved_by=resolved_by,
        ):
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
        if manifest.get("version") != 1:
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

    return {"manifest": manifest, "pages": pages}


def backup_page_filename(page_id: str) -> str:
    return f"{page_id.replace('/', '__')}.md"


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
