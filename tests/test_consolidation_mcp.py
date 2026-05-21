from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    return WorkerContext(config, repo, ledger, planner, audit_log, worker)


def rpc(client: TestClient, name: str, arguments: dict[str, Any] | None = None, request_id: int = 1):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
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
) -> None:
    repo.upsert_page(
        capture_id,
        f"""---
title: Capture {capture_id.rsplit("/", 1)[-1]}
kind: capture
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


def result_payload(response):
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "result" in payload, payload
    assert payload["result"]["isError"] is False
    return payload["result"]


def test_lore_consolidation_status_returns_expected_structure(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/status", summary="Lore MCP status works.")
    ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result = result_payload(rpc(client, "lore_consolidation_status"))

    content = result["structuredContent"]
    assert content["last_run"]["batch_id"]
    assert content["plans_by_status"]["applied"] == 1
    assert content["stuck_runs"] == []
    assert "Last run:" in result["content"][0]["text"]


def test_lore_consolidation_run_dry_run_returns_plans_without_auto_apply(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/run", summary="Lore MCP run creates plans.")

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result = result_payload(
            rpc(client, "lore_consolidation_run", {"dry_run": True, "batch_size": 10, "max_auto_apply": 5})
        )

    content = result["structuredContent"]
    assert content["captures_processed"] == 1
    assert content["plans_generated"] == 1
    assert content["auto_applied"] == 0
    assert content["dry_run"] is True
    assert ctx.repo.read_page("services/lore") is not None
    assert "Lore MCP run creates plans." not in ctx.repo.read_page("services/lore").content


def test_lore_list_patch_plans_returns_plans_after_run(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/list", summary="Lore MCP lists patch plans.")

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result_payload(rpc(client, "lore_consolidation_run", {"dry_run": True}))
        result = result_payload(rpc(client, "lore_list_patch_plans", {"status": "pending"}))

    content = result["structuredContent"]
    assert content["count"] == 1
    assert content["plans"][0]["target_page_id"] == "services/lore"
    assert "patch plan(s)" in result["content"][0]["text"]


def test_lore_preview_patch_returns_content_and_diff(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/preview", summary="Lore MCP previews diffs.")
    ctx.worker.run(dry_run=True, batch_size=10, max_auto_apply=5)
    [plan] = ctx.ledger.list_patch_plans(status="pending")

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result = result_payload(rpc(client, "lore_preview_patch", {"plan_id": plan["plan_id"]}))

    content = result["structuredContent"]
    assert content["plan_id"] == plan["plan_id"]
    assert "current_content" in content
    assert "proposed_content" in content
    assert "Lore MCP previews diffs." in content["unified_diff"]


def test_lore_apply_patch_applies_pending_plan(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/apply", summary="Lore MCP applies pending plans.")
    ctx.worker.run(dry_run=True, batch_size=10, max_auto_apply=5)
    [plan] = ctx.ledger.list_patch_plans(status="pending")

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result = result_payload(rpc(client, "lore_apply_patch", {"plan_id": plan["plan_id"]}))

    content = result["structuredContent"]
    page = ctx.repo.read_page("services/lore")
    assert content["target_page_id"] == "services/lore"
    assert ctx.ledger.get_patch_plan(plan["plan_id"])["status"] == "applied"
    assert page is not None
    assert "Lore MCP applies pending plans." in page.content


def test_lore_reject_patch_rejects_pending_plan(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/reject", summary="Lore MCP rejects pending plans.")
    ctx.worker.run(dry_run=True, batch_size=10, max_auto_apply=5)
    [plan] = ctx.ledger.list_patch_plans(status="pending")

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result = result_payload(rpc(client, "lore_reject_patch", {"plan_id": plan["plan_id"], "reason": "covered"}))

    content = result["structuredContent"]
    assert content == {"plan_id": plan["plan_id"], "status": "rejected", "reason": "covered"}
    assert ctx.ledger.get_patch_plan(plan["plan_id"])["status"] == "rejected"


def test_lore_review_batch_groups_plans_by_risk_and_recommends(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    write_page(ctx.repo, "decisions/routing", kind="decision")
    add_capture(ctx.repo, "inbox/2026-05-10/review-low", summary="Lore MCP reviews low risk batches.")
    add_capture(
        ctx.repo,
        "inbox/2026-05-10/review-decision",
        target="decisions/routing",
            summary="Decision patches require audit.",
    )
    run_result = ctx.worker.run(dry_run=True, batch_size=10, max_auto_apply=5)

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result = result_payload(rpc(client, "lore_review_batch", {"batch_id": run_result.batch_id}))

    content = result["structuredContent"]
    assert content["batch_id"] == run_result.batch_id
    assert content["total_plans"] == 2
    assert len(content["by_risk"]["low"]) == 1
    assert len(content["by_risk"]["medium"]) == 1
    assert content["auto_appliable_count"] == 1
    assert content["review_required_count"] == 1
    assert any("Requires audit" in item for item in content["recommendations"])
    assert "flow_000XXX" in result["content"][0]["text"]


def test_lore_consolidation_rollback_rolls_back_applied_plan(tmp_path, monkeypatch):
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
    add_capture(ctx.repo, "inbox/2026-05-10/rollback", summary="Lore MCP rolls back patches.")
    ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)
    [plan] = ctx.ledger.list_patch_plans(status="applied")

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        result = result_payload(rpc(client, "lore_consolidation_rollback", {"plan_id": plan["plan_id"]}))

    content = result["structuredContent"]
    page = ctx.repo.read_page("services/lore")
    assert content["plan_id"] == plan["plan_id"]
    assert content["page_id"] == "services/lore"
    assert page is not None
    assert page.content == original_content
    assert ctx.ledger.get_patch_plan(plan["plan_id"])["status"] == "rolled_back"
