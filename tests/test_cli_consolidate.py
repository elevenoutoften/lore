from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

from lore_app.cli import cmd_consolidate, cmd_status
from lore_app.repository import LoreRepository

if TYPE_CHECKING:
    from pathlib import Path


def configure_env(tmp_path, monkeypatch) -> tuple[LoreRepository, Path]:
    content_dir = tmp_path / "pages"
    monkeypatch.setenv("LORE_CONTENT_DIR", str(content_dir))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vectors.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))

    repo = LoreRepository(content_dir)
    repo.ensure_root()
    return repo, content_dir


def write_page(repo: LoreRepository, page_id: str) -> None:
    repo.upsert_page(
        page_id,
        f"""---
title: {page_id.rsplit("/", 1)[-1].title()}
kind: service
visibility: internal
status: active
---

# {page_id.rsplit("/", 1)[-1].title()}

## Facts

Existing fact.
""",
    )


def add_capture(repo: LoreRepository, capture_id: str, *, summary: str) -> None:
    repo.upsert_page(
        capture_id,
        f"""---
title: CLI Capture
kind: capture
visibility: internal
status: draft
summary: {summary}
confidence: high
actor: nyx
lane: project
observed_at: 2026-05-10T00:00:00+00:00
suggested_target_page: services/lore
---

# CLI Capture

{summary}
""",
    )


def test_cmd_consolidate_runs_dry_run_and_apply(tmp_path, monkeypatch, capsys):
    repo, _content_dir = configure_env(tmp_path, monkeypatch)
    write_page(repo, "services/lore")
    add_capture(
        repo,
        "inbox/2026-05-10/cli-dry-run",
        summary="Lore CLI dry run emits consolidation JSON.",
    )

    dry_args = argparse.Namespace(
        apply=False,
        max_auto_apply=0,
        batch_size=10,
        force_reextract=False,
    )
    assert cmd_consolidate(dry_args) == 0
    dry_output = json.loads(capsys.readouterr().out)

    assert dry_output["captures_processed"] >= 1
    assert dry_output["dry_run"] is True

    add_capture(
        repo,
        "inbox/2026-05-10/cli-apply",
        summary="Lore CLI apply emits non dry run JSON.",
    )
    apply_args = argparse.Namespace(
        apply=True,
        max_auto_apply=0,
        batch_size=10,
        force_reextract=False,
    )
    assert cmd_consolidate(apply_args) == 0
    apply_output = json.loads(capsys.readouterr().out)

    assert apply_output["dry_run"] is False


def test_cmd_status_returns_fresh_db_json(tmp_path, monkeypatch, capsys):
    configure_env(tmp_path, monkeypatch)

    assert cmd_status(argparse.Namespace()) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["last_run"] is None
    assert output["pending_captures"] == 0
    assert output["plans_by_status"]["draft"] == 0
    assert output["plans_by_status"]["applied"] == 0
    # 'needs_manual_review' is the real human-review queue status; 'review' is
    # not a stored PatchPlanStatus and was a dead key in the old seed.
    assert output["plans_by_status"]["needs_manual_review"] == 0
    assert output["plans_by_status"]["rejected"] == 0
    assert output["stuck_runs"] == 0
