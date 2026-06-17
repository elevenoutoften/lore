from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import dataclass
from unittest import mock

from fastapi.testclient import TestClient
from tests.test_extraction import valid_llm_response

from lore_app.audit import AuditLog
from lore_app.cli import cmd_status
from lore_app.config import LoreConfig
from lore_app.consolidation_worker import ConsolidationWorker
from lore_app.ledger import LedgerDB
from lore_app.llm_provider import FallbackLLMClient
from lore_app.main import create_app
from lore_app.patch_planner import PatchPlanner
from lore_app.repository import LoreRepository
from lore_app.schemas import ExtractedClaim, ExtractionResult


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


def store_claims(
    ledger: LedgerDB,
    batch_id: str,
    capture_ids: list[str],
    claims: list[ExtractedClaim],
) -> None:
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id=batch_id,
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=capture_ids,
            claims=claims,
            entities=[],
            edges=[],
            invalidations=[],
        )
    )


def count_rows(ledger: LedgerDB, table: str) -> int:
    row = ledger.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def make_claim(
    subject: str,
    obj: str,
    *,
    source_page_ids: list[str] | None = None,
    confidence: str = "high",
) -> ExtractedClaim:
    return ExtractedClaim(
        subject=subject,
        predicate="states",
        object=obj,
        confidence=confidence,
        actor="nyx",
        lane="project",
        observed_at="2026-05-10T00:00:00+00:00",
        source_page_ids=source_page_ids or [],
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
    assert ctx.ledger.list_patch_plans(status="pending") == []


def test_dry_run_does_not_persist_state(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/dry-run-state", summary="Lore dry runs are repeatable.")

    dry_run = ctx.worker.run(dry_run=True, batch_size=10, max_auto_apply=5)

    assert dry_run.dry_run is True
    assert dry_run.captures_processed == 1
    assert dry_run.plans_generated == 1
    assert dry_run.auto_applied == 0
    assert count_rows(ctx.ledger, "extraction_candidates") == 0
    assert count_rows(ctx.ledger, "extraction_batches") == 0
    assert count_rows(ctx.ledger, "patch_plans") == 0
    assert count_rows(ctx.ledger, "consolidation_runs") == 0
    assert ctx.ledger.list_traces(actor="consolidation-worker") == []

    apply_run = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)

    assert apply_run.dry_run is False
    assert apply_run.captures_processed == 1
    assert apply_run.plans_generated == 1
    assert count_rows(ctx.ledger, "extraction_candidates") > 0
    assert count_rows(ctx.ledger, "extraction_batches") == 1
    assert ctx.ledger.list_patch_plans()


def test_status_includes_all_fields(tmp_path, monkeypatch, capsys):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/status-fields", summary="Lore status has all fields.")
    ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=0)

    assert cmd_status(Namespace()) == 0
    payload = json.loads(capsys.readouterr().out)

    assert set(payload) == {
        "last_run",
        "pending_captures",
        "plans_by_status",
        "generated_plans",
        "auto_applied",
        "review_required",
        "errors",
        "stuck_runs",
    }
    assert payload["generated_plans"] == 1
    assert payload["auto_applied"] == 0
    assert payload["review_required"] == 1
    assert payload["errors"] == []
    assert payload["stuck_runs"] == 0


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

    [run_trace] = ctx.ledger.list_traces(actor="consolidation-worker")
    assert run_trace.status == "completed"
    assert run_trace.tool_refs[0].tool == "consolidation-worker"
    assert run_trace.outcome == f"batch_id={result.batch_id}"


def test_auto_apply_creates_trace_with_plan_trace_completed(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/auto-trace", summary="Lore completes auto-apply traces.")

    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=1)
    [plan_row] = ctx.ledger.list_patch_plans(status="applied")
    plan_trace = ctx.ledger.get_trace(plan_row["trace_id"])

    assert result.auto_applied == 1
    assert plan_trace is not None
    assert plan_trace.status == "completed"
    assert "Applied" in plan_trace.outcome


def test_plan_batch_creates_trace_for_each_plan(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore-a")
    write_page(ctx.repo, "services/lore-b")
    add_capture(ctx.repo, "inbox/2026-05-10/trace-a", target="services/lore-a", summary="Lore A has traces.")
    add_capture(ctx.repo, "inbox/2026-05-10/trace-b", target="services/lore-b", summary="Lore B has traces.")
    store_claims(
        ctx.ledger,
        "batch-traces",
        ["inbox/2026-05-10/trace-a", "inbox/2026-05-10/trace-b"],
        [
            make_claim("services/lore-a", "Lore A has traces", source_page_ids=["services/lore-a"]),
            make_claim("services/lore-b", "Lore B has traces", source_page_ids=["services/lore-b"]),
        ],
    )

    plans = ctx.planner.plan_batch(batch_id="batch-traces")
    traces = ctx.ledger.list_traces(actor="patch-planner")
    traces_by_id = {trace.trace_id: trace for trace in traces}

    assert len(plans) == 2
    assert all(plan.trace_id for plan in plans)
    assert {plan.trace_id for plan in plans} <= set(traces_by_id)
    for plan in plans:
        trace = traces_by_id[plan.trace_id]
        assert trace.status == "active"
        assert trace.outcome == "plan_generated"
        assert trace.related_ids["page_id"] == plan.target_page_id
        assert trace.related_ids["candidate_ids"] == ",".join(plan.candidate_ids)
        assert [ref.id for ref in trace.context_refs] == plan.candidate_ids
        assert f"{plan.risk_level.value} risk" in trace.reason_summary
        assert f"auto_appliable={plan.auto_appliable}" in trace.reason_summary


def test_apply_and_reject_update_plan_trace_status(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    write_page(ctx.repo, "decisions/agent-routing", kind="decision")
    add_capture(ctx.repo, "inbox/2026-05-10/trace-apply", target="services/lore", summary="Lore applies traces.")
    add_capture(
        ctx.repo,
        "inbox/2026-05-10/trace-reject",
        target="decisions/agent-routing",
        summary="Decision traces need review.",
    )
    store_claims(
        ctx.ledger,
        "batch-trace-apply",
        ["inbox/2026-05-10/trace-apply"],
        [make_claim("services/lore", "Lore applies traces", source_page_ids=["services/lore"])],
    )
    store_claims(
        ctx.ledger,
        "batch-trace-reject",
        ["inbox/2026-05-10/trace-reject"],
        [
            make_claim(
                "decisions/agent-routing",
                "Decision traces need review",
                source_page_ids=["decisions/agent-routing"],
            )
        ],
    )

    [apply_plan] = ctx.planner.plan_batch(batch_id="batch-trace-apply")
    ctx.planner.apply_plan(apply_plan.plan_id)
    apply_trace = ctx.ledger.get_trace(apply_plan.trace_id)

    [reject_plan] = ctx.planner.plan_batch(batch_id="batch-trace-reject")
    ctx.planner.reject_plan(reject_plan.plan_id, reason="manual review required")
    reject_trace = ctx.ledger.get_trace(reject_plan.trace_id)

    assert apply_trace is not None
    assert apply_trace.status == "completed"
    assert apply_trace.outcome == f"Applied {apply_plan.operation} to {apply_plan.target_page_id}"
    assert reject_trace is not None
    assert reject_trace.status == "abandoned"
    assert reject_trace.outcome == "Rejected: manual review required"


def test_rejection_creates_abandoned_trace(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "decisions/agent-routing", kind="decision")
    add_capture(
        ctx.repo,
        "inbox/2026-05-10/reject-trace",
        target="decisions/agent-routing",
        summary="Decision consolidation needs review.",
    )

    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)
    [plan_row] = ctx.ledger.list_patch_plans(status="pending")
    ctx.planner.reject_plan(plan_row["plan_id"], reason="manual review required")
    plan_trace = ctx.ledger.get_trace(plan_row["trace_id"])

    assert result.auto_applied == 0
    assert plan_trace is not None
    assert plan_trace.status == "abandoned"


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


def test_apply_failure_creates_trace(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/apply-failure", summary="Lore apply failure is traced.")

    def failing_apply(plan_id: str, *, force: bool = False):
        raise RuntimeError("apply exploded")

    monkeypatch.setattr(ctx.planner, "apply_plan", failing_apply)
    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=1)
    traces = ctx.ledger.list_traces(actor="consolidation-worker")

    assert result.auto_applied == 0
    assert any("apply exploded" in error for error in result.errors)
    failure_traces = [trace for trace in traces if trace.status == "failed" and "Apply failed" in trace.reason_summary]
    assert len(failure_traces) == 1
    assert failure_traces[0].tool_refs[0].action == "apply"


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


def test_rollback_creates_trace(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/rollback-trace", summary="Lore rollback traces exist.")
    ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=1)
    [plan] = ctx.ledger.list_patch_plans(status="applied")

    ctx.worker.rollback(plan["plan_id"])
    plan_row = ctx.ledger.get_patch_plan(plan["plan_id"])
    assert plan_row is not None
    plan_trace = ctx.ledger.get_trace(plan_row["trace_id"])
    worker_traces = ctx.ledger.list_traces(actor="consolidation-worker")

    assert plan_trace is not None
    assert plan_trace.status == "completed"
    assert "rolled_back" in plan_trace.outcome
    assert any("Rolled back plan" in trace.reason_summary for trace in worker_traces)


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


def test_run_plans_candidates_left_by_a_prior_separate_extraction(tmp_path, monkeypatch):
    """Candidates extracted by a separate /api/extraction/run must not be stranded."""
    from lore_app.extraction import extract_from_captures

    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/prior", summary="Lore handled a prior extraction note.")

    # Simulate POST /api/extraction/run having run earlier and on its own.
    extract_from_captures(ctx.repo, dry_run=False, ledger_db=ctx.ledger)

    # Consolidation now finds nothing new to extract, but the candidate is pending.
    result = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)

    assert result.plans_generated >= 1
    assert result.auto_applied >= 1
    page = ctx.repo.read_page("services/lore")
    assert page is not None
    assert "prior extraction note" in page.content.lower()


def test_force_reextract_still_produces_plans(tmp_path, monkeypatch):
    """force_reextract reinforces candidates under their old batch id; planning must still find them."""
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/reproc", summary="Lore should reprocess cleanly.")

    first = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)
    assert first.plans_generated >= 1

    reprocessed = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5, force_reextract=True)
    assert reprocessed.plans_generated >= 1  # was silently 0 before the no-batch-filter fix


def test_force_reextract_does_not_duplicate_already_applied_fact(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    write_page(ctx.repo, "services/lore")
    add_capture(
        ctx.repo,
        "inbox/2026-05-10/idempotent",
        summary="Lore force re-extract stays content-idempotent.",
    )
    fact = "services/lore states Lore force re-extract stays content-idempotent."

    first = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5)
    second = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5, force_reextract=True)
    third = ctx.worker.run(dry_run=False, batch_size=10, max_auto_apply=5, force_reextract=True)
    page = ctx.repo.read_page("services/lore")

    assert first.auto_applied == 1
    assert second.plans_generated >= 1
    assert third.plans_generated >= 1
    assert page is not None
    assert page.content.count(fact) == 1


def _stub_llm_client(capture_id: str, fact: str) -> FallbackLLMClient:
    primary = mock.MagicMock()
    primary.extract_json.return_value = valid_llm_response(capture_id, fact)
    return FallbackLLMClient(primary=primary, escalation=None)


def test_run_passes_explicit_llm_client_to_extraction(tmp_path, monkeypatch):
    # Regression: the worker used to drop the LLM client, so auto-consolidation
    # and the scheduled runner always extracted deterministically regardless of
    # the configured provider.
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/llm-explicit")
    client = _stub_llm_client("inbox/llm-explicit", "Lore runs LLM-backed extraction.")

    ctx.worker.run(dry_run=True, batch_size=10, llm_client=client)

    assert client.primary.extract_json.call_count >= 1


def test_run_resolves_llm_client_from_provider(tmp_path, monkeypatch):
    # The web app supplies the live client via llm_client_provider so hot-reloaded
    # settings take effect without re-creating the worker.
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/llm-provider")
    client = _stub_llm_client("inbox/llm-provider", "Lore runs LLM-backed extraction.")
    worker = ConsolidationWorker(
        ctx.repo,
        ctx.ledger,
        ctx.planner,
        ctx.config,
        ctx.audit_log,
        llm_client_provider=lambda: client,
    )

    worker.run(dry_run=True, batch_size=10)

    assert client.primary.extract_json.call_count >= 1
