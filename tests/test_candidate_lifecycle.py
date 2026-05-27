from __future__ import annotations

from dataclasses import dataclass

import pytest

from lore_app.audit import AuditLog
from lore_app.config import LoreConfig
from lore_app.consolidation_worker import ConsolidationWorker
from lore_app.ledger import LedgerDB
from lore_app.patch_planner import PatchPlanner
from lore_app.repository import LoreRepository
from lore_app.schemas import ExtractedClaim, ExtractionResult


@dataclass
class PlannerContext:
    repo: LoreRepository
    ledger: LedgerDB
    planner: PatchPlanner
    config: LoreConfig
    audit_log: AuditLog


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> PlannerContext:
    content_dir = tmp_path / "pages"
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("LORE_CONTENT_DIR", str(content_dir))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vector.db"))

    config = LoreConfig()
    repo = LoreRepository(config.content_dir)
    repo.ensure_root()
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    audit_log = AuditLog(audit_dir)
    planner = PatchPlanner(repo, ledger, audit_log=audit_log)
    return PlannerContext(repo=repo, ledger=ledger, planner=planner, config=config, audit_log=audit_log)


def add_capture(
    repo: LoreRepository,
    capture_id: str,
    *,
    title: str,
    summary: str,
    suggested_target_page: str | None = None,
    kind: str = "capture",
) -> None:
    suggested = f"suggested_target_page: {suggested_target_page}\n" if suggested_target_page else ""
    repo.upsert_page(
        capture_id,
        f"""---
title: {title}
kind: {kind}
visibility: internal
status: draft
summary: {summary}
confidence: high
actor: nyx
lane: project
observed_at: 2026-05-10T00:00:00+00:00
{suggested}---

# {title}

{summary}
""",
    )


def make_claim(subject: str, obj: str, *, source_page_ids: list[str]) -> ExtractedClaim:
    return ExtractedClaim(
        subject=subject,
        predicate="states",
        object=obj,
        confidence="high",
        actor="nyx",
        lane="project",
        observed_at="2026-05-10T00:00:00+00:00",
        source_page_ids=source_page_ids,
    )


def store_claim(ctx: PlannerContext, batch_id: str, capture_id: str, claim: ExtractedClaim) -> None:
    ctx.ledger.store_extraction_result(
        ExtractionResult(
            batch_id=batch_id,
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=[capture_id],
            claims=[claim],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )


def candidate_status(ledger: LedgerDB, candidate_id: str) -> str | None:
    row = ledger.connection.execute(
        "SELECT status FROM extraction_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return str(row["status"]) if row else None


# --- flow_000384: Candidates transition to active on apply, rejected on reject ---


def test_candidates_transition_to_active_on_apply(ctx):
    capture_id = "inbox/2026-05-10/test-svc"
    add_capture(
        ctx.repo,
        capture_id,
        title="Test Service",
        summary="Test service has a new API endpoint.",
        suggested_target_page="services/test-svc",
    )
    store_claim(
        ctx,
        "batch-lifecycle",
        capture_id,
        make_claim(
            "services/test-svc",
            "Test service has a new API endpoint.",
            source_page_ids=[capture_id],
        ),
    )

    plans = ctx.planner.plan_batch(batch_id="batch-lifecycle")
    assert len(plans) == 1
    plan = plans[0]
    candidate_id = plan.candidate_ids[0]

    # Before apply, candidate status is 'candidate'
    assert candidate_status(ctx.ledger, candidate_id) == "candidate"

    ctx.planner.apply_plan(plan.plan_id)

    # After apply, candidate status is 'active'
    assert candidate_status(ctx.ledger, candidate_id) == "active"

    # Re-planning the same batch should not duplicate plans
    new_plans = ctx.planner.plan_batch(batch_id="batch-lifecycle")
    assert new_plans == []


def test_candidates_transition_to_rejected_on_reject(ctx):
    capture_id = "inbox/2026-05-10/reject-svc"
    add_capture(
        ctx.repo,
        capture_id,
        title="Reject Service",
        summary="Should be rejected.",
        suggested_target_page="services/reject-svc",
    )
    store_claim(
        ctx,
        "batch-reject",
        capture_id,
        make_claim(
            "services/reject-svc",
            "Should be rejected.",
            source_page_ids=[capture_id],
        ),
    )

    plans = ctx.planner.plan_batch(batch_id="batch-reject")
    assert len(plans) == 1
    candidate_id = plans[0].candidate_ids[0]

    ctx.planner.reject_plan(plans[0].plan_id, reason="Low confidence")

    assert candidate_status(ctx.ledger, candidate_id) == "rejected"

    # Re-planning should not pick up rejected candidates
    new_plans = ctx.planner.plan_batch(batch_id="batch-reject")
    assert new_plans == []


# --- flow_000385: Direct apply is rollback-safe (before_content in audit) ---


def test_direct_apply_rollback_updates_existing_page(ctx):
    """Create an existing page, apply an update via direct planner, then rollback via ConsolidationWorker."""
    # Create an existing page first
    ctx.repo.upsert_page(
        "services/existing-svc",
        """---
title: Existing Service
kind: service
visibility: internal
status: active
summary: Original summary.
---

# Existing Service

Original content.
""",
    )

    capture_id = "inbox/2026-05-10/update-svc"
    add_capture(
        ctx.repo,
        capture_id,
        title="Update Service",
        summary="Updated summary for existing service.",
        suggested_target_page="services/existing-svc",
    )
    store_claim(
        ctx,
        "batch-update",
        capture_id,
        make_claim(
            "services/existing-svc",
            "Updated summary for existing service.",
            source_page_ids=[capture_id],
        ),
    )

    plans = ctx.planner.plan_batch(batch_id="batch-update")
    assert len(plans) == 1
    plan = plans[0]

    original_content = ctx.repo.read_page("services/existing-svc").content

    # Apply directly via PatchPlanner (not through ConsolidationWorker)
    ctx.planner.apply_plan(plan.plan_id)

    # Page should now have updated content
    updated_page = ctx.repo.read_page("services/existing-svc")
    assert updated_page is not None
    assert updated_page.content != original_content

    # Now rollback via ConsolidationWorker — should find before_content in audit
    worker = ConsolidationWorker(ctx.repo, ctx.ledger, ctx.planner, ctx.config, audit_log=ctx.audit_log)
    rollback_result = worker.rollback(plan.plan_id)

    assert rollback_result.plan_id == plan.plan_id
    assert rollback_result.page_id == "services/existing-svc"

    # After rollback, page should be restored to original content
    restored_page = ctx.repo.read_page("services/existing-svc")
    assert restored_page is not None
    assert restored_page.content.strip() == original_content.strip()


def test_direct_apply_audit_contains_before_content(ctx):
    """Verify the audit summary JSON includes before_content for direct applies."""
    capture_id = "inbox/2026-05-10/audit-svc"
    add_capture(
        ctx.repo,
        capture_id,
        title="Audit Service",
        summary="Audit service records before content.",
        suggested_target_page="services/audit-svc",
    )
    store_claim(
        ctx,
        "batch-audit",
        capture_id,
        make_claim(
            "services/audit-svc",
            "Audit service records before content.",
            source_page_ids=[capture_id],
        ),
    )

    plans = ctx.planner.plan_batch(batch_id="batch-audit")
    plan = plans[0]

    # Apply directly (no ConsolidationWorker involved)
    ctx.planner.apply_plan(plan.plan_id)

    # Find the audit record via ConsolidationWorker
    worker = ConsolidationWorker(ctx.repo, ctx.ledger, ctx.planner, ctx.config, audit_log=ctx.audit_log)
    payload = worker._find_rollback_payload(plan.plan_id)

    assert payload is not None, "Expected to find rollback payload with before_content"
    assert "before_content" in payload, "Audit payload must contain before_content for direct apply rollback"
