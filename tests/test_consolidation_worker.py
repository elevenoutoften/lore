from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from lore_app.audit import AuditLog
from lore_app.config import LoreConfig
from lore_app.consolidation_worker import ConsolidationWorker
from lore_app.ledger import LedgerDB
from lore_app.main import create_app
from lore_app.patch_planner import PatchPlanner
from lore_app.repository import LoreRepository


@dataclass
class WorkerContext:
    config: LoreConfig
    repo: LoreRepository
    ledger: LedgerDB
    planner: PatchPlanner
    audit_log: AuditLog
    worker: ConsolidationWorker


def make_context(tmp_path, monkeypatch) -> WorkerContext:
    content_dir = tmp_path / "pages"
    for subdir in ("services", "decisions", "runbooks", "inbox"):
        (content_dir / subdir).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("LORE_CONTENT_DIR", str(content_dir))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vector.db"))

    config = LoreConfig()
    repo = LoreRepository(config.content_dir)
    repo.ensure_root()
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    audit_log = AuditLog(config.content_dir / ".lore" / "audit")
    planner = PatchPlanner(repo, ledger, audit_log)
    worker = ConsolidationWorker(repo, ledger, planner, config, audit_log)
    return WorkerContext(
        config=config,
        repo=repo,
        ledger=ledger,
        planner=planner,
        audit_log=audit_log,
        worker=worker,
    )


def write_page(repo: LoreRepository, page_id: str, *, kind: str = "service", body: str = "Existing fact.") -> None:
    repo.upsert_page(
        page_id,
        f"""---
title: {page_id.rsplit("/", 1)[-1].title()}
kind: {kind}
visibility: internal
status: active
---

# {page_id.rsplit("/", 1)[-1].title()}

## Facts

{body}
""",
    )


def add_capture(
    repo: LoreRepository,
    capture_id: str,
    *,
    target: str = "services/lore",
    summary: str = "Lore has a consolidation worker.",
    kind: str = "capture",
) -> None:
    repo.upsert_page(
        capture_id,
        f"""---
title: Capture {capture_id.rsplit("/", 1)[-1]}
kind: {kind}
visibility: internal
status: draft
summary: {summary}
confidence: high
actor: nyx
lane: project
observed_at: 2026-05-10T00:00:00+00:00
suggested_target_page: {target}
---

# Capture

{summary}
""",
    )


def test_run_dry_run_extracts_and_plans_without_applying(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/dry-run", summary="Lore plans safe patches.")

    result = ctx.worker.run(dry_run=True, batch_size=10, max_auto_apply=5)
    page = ctx.repo.read_page("services/lore")

    assert result.captures_processed == 1
    assert result.candidates_extracted >= 1
    assert result.plans_generated == 1
    assert result.auto_applied == 0
    assert page is not None
    assert "Lore plans safe patches." not in page.content
    assert ctx.ledger.list_patch_plans(status="pending")


def test_run_auto_applies_safe_plans(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/auto", summary="Lore auto-applies low risk facts.")

    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)
    page = ctx.repo.read_page("services/lore")

    assert result.auto_applied == 1
    assert result.review_required == 0
    assert page is not None
    assert "Lore auto-applies low risk facts." in page.content
    assert ctx.ledger.list_patch_plans(status="applied")


def test_run_respects_max_auto_apply_limit(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore-a")
    write_page(ctx.repo, "services/lore-b")
    add_capture(ctx.repo, "inbox/2026-05-10/limit-a", target="services/lore-a", summary="Lore A has fact.")
    add_capture(ctx.repo, "inbox/2026-05-10/limit-b", target="services/lore-b", summary="Lore B has fact.")

    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=1)

    assert result.plans_generated == 2
    assert result.auto_applied == 1
    assert len(ctx.ledger.list_patch_plans(status="applied")) == 1
    assert len(ctx.ledger.list_patch_plans(status="pending")) == 1


def test_run_skips_auto_appliable_decision_and_runbook_plans(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "decisions/agent-routing", kind="decision")
    write_page(ctx.repo, "runbooks/lore-ops", kind="runbook")
    add_capture(
        ctx.repo,
        "inbox/2026-05-10/decision",
        target="decisions/agent-routing",
        summary="Decision needs a human.",
    )
    add_capture(
        ctx.repo,
        "inbox/2026-05-10/runbook",
        target="runbooks/lore-ops",
        summary="Runbook needs a human.",
    )

    original_plan_batch = ctx.planner.plan_batch

    def plan_batch_with_forced_auto(*args, **kwargs):
        plans = original_plan_batch(*args, **kwargs)
        for plan in plans:
            plan.auto_appliable = True
            ctx.ledger.store_patch_plan(plan, batch_id=kwargs.get("batch_id"))
        return plans

    monkeypatch.setattr(ctx.planner, "plan_batch", plan_batch_with_forced_auto)
    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)

    assert result.plans_generated == 2
    assert result.auto_applied == 0
    assert len(ctx.ledger.list_patch_plans(status="pending")) == 2


def test_run_collects_apply_errors_and_continues(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore-a")
    write_page(ctx.repo, "services/lore-b")
    add_capture(ctx.repo, "inbox/2026-05-10/error-a", target="services/lore-a", summary="Lore A applies.")
    add_capture(ctx.repo, "inbox/2026-05-10/error-b", target="services/lore-b", summary="Lore B applies.")
    original_apply = ctx.planner.apply_plan
    calls = 0

    def flaky_apply(plan_id: str, *, force: bool = False):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return original_apply(plan_id, force=force)

    monkeypatch.setattr(ctx.planner, "apply_plan", flaky_apply)
    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)

    assert result.auto_applied == 1
    assert any("boom" in error for error in result.errors)
    assert len(ctx.ledger.list_patch_plans(status="applied")) == 1


def test_rollback_restores_page_content_and_updates_status(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    original_content = """---
title: Lore
kind: service
visibility: internal
status: active
---

# Lore

## Facts

Original fact.
"""
    ctx.repo.upsert_page("services/lore", original_content)
    add_capture(ctx.repo, "inbox/2026-05-10/rollback", summary="Lore rollback fact.")
    run_result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)
    [plan] = ctx.ledger.list_patch_plans(status="applied")

    rollback = ctx.worker.rollback(plan["plan_id"])
    page = ctx.repo.read_page("services/lore")

    assert run_result.auto_applied == 1
    assert page is not None
    assert page.content == original_content
    assert rollback.plan_id == plan["plan_id"]
    assert rollback.page_id == "services/lore"
    assert rollback.before_hash != rollback.after_hash
    assert ctx.ledger.get_patch_plan(plan["plan_id"])["status"] == "rolled_back"


def test_consolidation_status_endpoint_returns_metrics(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/status", summary="Lore status metrics exist.")
    ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        response = client.get("/api/consolidation/status")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["last_run"]["batch_id"]
    assert payload["plans_by_status"]["applied"] == 1
    assert payload["total_captures"] == 1
    assert payload["stuck_runs"] == []


def test_consolidation_run_api_endpoint(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/api-run", summary="Lore run endpoint works.")

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/consolidation/run",
            json={"dry_run": False, "batch_size": 10, "max_auto_apply": 5},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["captures_processed"] == 1
    assert payload["plans_generated"] == 1
    assert payload["auto_applied"] == 1


def test_consolidation_run_api_force_reextracts_processed_captures(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/force", summary="Lore force re-extracts captures.")
    ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=0)

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        skipped = client.post(
            "/api/consolidation/run",
            json={"dry_run": False, "batch_size": 10, "max_auto_apply": 0},
        )
        forced = client.post(
            "/api/consolidation/run",
            json={"dry_run": False, "batch_size": 10, "max_auto_apply": 0, "force_reextract": True},
        )

    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["captures_processed"] == 0
    assert forced.status_code == 200, forced.text
    assert forced.json()["captures_processed"] == 1
