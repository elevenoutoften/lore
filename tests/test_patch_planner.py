from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

import lore_app.ledger as ledger_module
from lore_app.audit import AuditLog
from lore_app.config import LoreConfig
from lore_app.ledger import LedgerDB
from lore_app.main import create_app
from lore_app.patch_planner import PatchPlanner
from lore_app.repository import LoreRepository
from lore_app.schemas import ExtractedClaim, ExtractionResult, PatchOperation, RiskLevel


@dataclass
class PlannerContext:
    config: LoreConfig
    repo: LoreRepository
    ledger: LedgerDB
    planner: PatchPlanner
    audit_log: AuditLog


@pytest.fixture(autouse=True)
def patch_ledger_row_decoder(monkeypatch):
    def decode_row(row):
        decoded = dict(row)
        for key in ("content_json", "source_capture_ids", "source_page_ids", "candidate_ids", "policies_applied"):
            if key in decoded and isinstance(decoded[key], str):
                with contextlib.suppress(json.JSONDecodeError):
                    decoded[key] = json.loads(decoded[key])
        if "auto_appliable" in decoded:
            decoded["auto_appliable"] = bool(decoded["auto_appliable"])
        return decoded

    monkeypatch.setattr(ledger_module, "_decode_row", decode_row)


def make_context(tmp_path, monkeypatch) -> PlannerContext:
    content_dir = tmp_path / "pages"
    for subdir in ("services", "procedures", "decisions", "inbox"):
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
    return PlannerContext(config=config, repo=repo, ledger=ledger, planner=planner, audit_log=audit_log)


def write_page(repo: LoreRepository, page_id: str, body: str) -> None:
    repo.upsert_page(page_id, body)


def add_capture(
    repo: LoreRepository,
    capture_id: str,
    *,
    title: str = "Planner capture",
    kind: str = "capture",
    summary: str = "Planner summary",
    suggested_target_page: str = "services/lore",
    visibility: str = "internal",
) -> None:
    repo.upsert_page(
        capture_id,
        f"""---
title: {title}
kind: {kind}
visibility: {visibility}
status: draft
summary: {summary}
confidence: high
actor: nyx
lane: project
observed_at: 2026-05-10T00:00:00+00:00
suggested_target_page: {suggested_target_page}
---

# {title}

{summary}
""",
    )


def store_claims(ledger: LedgerDB, batch_id: str, capture_id: str, claims: list[ExtractedClaim]) -> None:
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id=batch_id,
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=[capture_id],
            claims=claims,
            entities=[],
            edges=[],
            invalidations=[],
        )
    )


def make_claim(
    subject: str,
    obj: str,
    *,
    predicate: str = "states",
    confidence: str = "high",
    source_page_ids: list[str] | None = None,
) -> ExtractedClaim:
    return ExtractedClaim(
        subject=subject,
        predicate=predicate,
        object=obj,
        confidence=confidence,
        actor="nyx",
        lane="project",
        observed_at="2026-05-10T00:00:00+00:00",
        source_page_ids=source_page_ids or [],
    )


def test_plan_batch_creates_expected_operations_risk_levels_and_auto_apply_flags(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/2026-05-10/planner-batch", suggested_target_page="services/new-worker")

    write_page(
        ctx.repo,
        "services/lore",
        """---
title: Lore
kind: service
visibility: internal
status: active
---

# Lore

Current service overview.
""",
    )
    write_page(
        ctx.repo,
        "procedures/deploy-lore",
        """---
title: Deploy Lore
kind: procedure
visibility: internal
status: accepted
---

# Deploy Lore

## Facts

Existing deployment fact.
""",
    )
    write_page(
        ctx.repo,
        "decisions/agent-routing",
        """---
title: Agent routing
kind: decision
visibility: internal
status: accepted
---

# Agent routing

## Facts

Current decision state.
""",
    )

    store_claims(
        ctx.ledger,
        "batch-ops",
        "inbox/2026-05-10/planner-batch",
        [
            make_claim("services/lore", "Lore uses a SQLite ledger", source_page_ids=["services/lore"]),
            make_claim(
                "procedures/deploy-lore",
                "Deploy Lore requires a smoke test",
                source_page_ids=["procedures/deploy-lore"],
            ),
            make_claim(
                "decisions/agent-routing",
                "Agent routing prefers deterministic fan-out",
                source_page_ids=["decisions/agent-routing"],
            ),
            make_claim("services/new-worker", "New worker handles patch consolidation", source_page_ids=[]),
        ],
    )

    plans = ctx.planner.plan_batch(batch_id="batch-ops")
    plans_by_target = {plan.target_page_id: plan for plan in plans}

    assert plans_by_target["services/lore"].operation == PatchOperation.insert_new_fact
    assert plans_by_target["services/lore"].risk_level == RiskLevel.low
    assert plans_by_target["services/lore"].auto_appliable is True

    assert plans_by_target["procedures/deploy-lore"].operation == PatchOperation.append_sourced_paragraph
    assert plans_by_target["procedures/deploy-lore"].risk_level == RiskLevel.low
    assert plans_by_target["procedures/deploy-lore"].auto_appliable is True

    assert plans_by_target["decisions/agent-routing"].operation == PatchOperation.append_sourced_paragraph
    assert plans_by_target["decisions/agent-routing"].risk_level == RiskLevel.medium
    assert plans_by_target["decisions/agent-routing"].auto_appliable is False

    assert plans_by_target["services/new-worker"].operation == PatchOperation.create_stub_page
    assert plans_by_target["services/new-worker"].risk_level == RiskLevel.low
    assert plans_by_target["services/new-worker"].auto_appliable is True


def test_plan_batch_marks_contradictions_high_risk_and_not_auto_appliable(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/2026-05-10/contradiction", suggested_target_page="procedures/deploy-lore")
    write_page(
        ctx.repo,
        "procedures/deploy-lore",
        """---
title: Deploy Lore
kind: procedure
visibility: internal
status: accepted
---

# Deploy Lore

## Facts

Use Docker Compose for deployment.
""",
    )

    store_claims(
        ctx.ledger,
        "batch-old",
        "inbox/2026-05-10/contradiction",
        [
            make_claim(
                "procedures/deploy-lore", "Deploy Lore uses Docker Compose", source_page_ids=["procedures/deploy-lore"]
            )
        ],
    )
    store_claims(
        ctx.ledger,
        "batch-new",
        "inbox/2026-05-10/contradiction",
        [
            make_claim(
                "procedures/deploy-lore", "Deploy Lore uses systemd units", source_page_ids=["procedures/deploy-lore"]
            )
        ],
    )

    [plan] = ctx.planner.plan_batch(batch_id="batch-new")

    assert plan.operation == PatchOperation.mark_stale
    assert plan.risk_level == RiskLevel.high
    assert plan.auto_appliable is False


def test_apply_plan_creates_stub_page_with_expected_frontmatter(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(
        ctx.repo,
        "inbox/2026-05-10/new-service",
        kind="service",
        summary="New service exposes patch consolidation APIs.",
        suggested_target_page="services/new-service",
    )
    store_claims(
        ctx.ledger,
        "batch-stub",
        "inbox/2026-05-10/new-service",
        [make_claim("services/new-service", "New service exposes patch consolidation APIs")],
    )

    [plan] = ctx.planner.plan_batch(batch_id="batch-stub")
    result = ctx.planner.apply_plan(plan.plan_id)
    page = ctx.repo.read_page("services/new-service")
    stored_plan = ctx.ledger.get_patch_plan(plan.plan_id)

    assert result.operation == PatchOperation.create_stub_page
    assert page is not None
    assert page.frontmatter["kind"] == "service"
    assert page.frontmatter["visibility"] == "internal"
    assert page.frontmatter["status"] == "draft"
    assert page.frontmatter["summary"] == "New service exposes patch consolidation APIs"
    assert page.frontmatter["sources"] == ["inbox/2026-05-10/new-service"]
    assert "## Facts" in page.content
    assert stored_plan["status"] == "applied"


def test_apply_plan_appends_paragraph_and_records_audit_hashes(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/2026-05-10/append", suggested_target_page="services/lore")
    write_page(
        ctx.repo,
        "services/lore",
        """---
title: Lore
kind: service
visibility: internal
status: active
---

# Lore

## Facts

Existing fact.
""",
    )
    store_claims(
        ctx.ledger,
        "batch-append",
        "inbox/2026-05-10/append",
        [make_claim("services/lore", "Lore stores sourced patch plans", source_page_ids=["services/lore"])],
    )

    [plan] = ctx.planner.plan_batch(batch_id="batch-append")
    result = ctx.planner.apply_plan(plan.plan_id)
    page = ctx.repo.read_page("services/lore")
    history = ctx.audit_log.page_history("services/lore")

    assert page is not None
    assert (
        "services/lore states Lore stores sourced patch plans. Observed at 2026-05-10T00:00:00+00:00." in page.content
    )
    assert "Source: [[inbox/2026-05-10/append]]." in page.content
    assert history

    summary = json.loads(history[0].summary)
    assert summary["plan_id"] == plan.plan_id
    assert summary["before_hash"] == result.before_hash
    assert summary["after_hash"] == result.after_hash
    assert result.before_hash != result.after_hash


def test_apply_plan_requires_force_for_non_auto_appliable_plans(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/2026-05-10/decision", suggested_target_page="decisions/agent-routing")
    write_page(
        ctx.repo,
        "decisions/agent-routing",
        """---
title: Agent routing
kind: decision
visibility: internal
status: accepted
---

# Agent routing

## Facts

Current decision state.
""",
    )
    store_claims(
        ctx.ledger,
        "batch-decision",
        "inbox/2026-05-10/decision",
        [
            make_claim(
                "decisions/agent-routing",
                "Agent routing prefers bounded retries",
                source_page_ids=["decisions/agent-routing"],
            )
        ],
    )

    [plan] = ctx.planner.plan_batch(batch_id="batch-decision")

    with pytest.raises(ValueError, match="not auto-appliable"):
        ctx.planner.apply_plan(plan.plan_id)

    result = ctx.planner.apply_plan(plan.plan_id, force=True)
    page = ctx.repo.read_page("decisions/agent-routing")

    assert result.auto_applied is False
    assert page is not None
    assert "Agent routing prefers bounded retries" in page.content


def test_plan_status_transitions_for_apply_and_reject(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/2026-05-10/status-a", suggested_target_page="services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/status-b", suggested_target_page="decisions/agent-routing")
    write_page(
        ctx.repo,
        "services/lore",
        """---
title: Lore
kind: service
visibility: internal
status: active
---

# Lore
""",
    )
    write_page(
        ctx.repo,
        "decisions/agent-routing",
        """---
title: Agent routing
kind: decision
visibility: internal
status: accepted
---

# Agent routing

## Facts

Current decision state.
""",
    )
    store_claims(
        ctx.ledger,
        "batch-status-a",
        "inbox/2026-05-10/status-a",
        [make_claim("services/lore", "Lore has a new consolidation worker", source_page_ids=["services/lore"])],
    )
    store_claims(
        ctx.ledger,
        "batch-status-b",
        "inbox/2026-05-10/status-b",
        [
            make_claim(
                "decisions/agent-routing",
                "Agent routing uses queue arbitration",
                source_page_ids=["decisions/agent-routing"],
            )
        ],
    )

    [apply_plan] = ctx.planner.plan_batch(batch_id="batch-status-a")
    assert ctx.ledger.get_patch_plan(apply_plan.plan_id)["status"] == "pending"
    ctx.planner.apply_plan(apply_plan.plan_id)
    assert ctx.ledger.get_patch_plan(apply_plan.plan_id)["status"] == "applied"

    [reject_plan] = ctx.planner.plan_batch(batch_id="batch-status-b")
    assert ctx.ledger.get_patch_plan(reject_plan.plan_id)["status"] == "pending"
    ctx.planner.reject_plan(reject_plan.plan_id, reason="manual review required")
    stored_plan = ctx.ledger.get_patch_plan(reject_plan.plan_id)
    candidate = ctx.ledger.get_candidates(candidate_type="claim", status="rejected")[0]

    assert stored_plan["status"] == "rejected"
    assert stored_plan["rejection_reason"] == "manual review required"
    assert candidate["invalidation_reason"] == "manual review required"


def test_consolidation_api_endpoints(tmp_path, monkeypatch):
    ctx = make_context(tmp_path, monkeypatch)
    add_capture(ctx.repo, "inbox/2026-05-10/api-apply", suggested_target_page="services/lore")
    add_capture(ctx.repo, "inbox/2026-05-10/api-reject", suggested_target_page="decisions/agent-routing")
    write_page(
        ctx.repo,
        "services/lore",
        """---
title: Lore
kind: service
visibility: internal
status: active
---

# Lore

## Facts

Existing fact.
""",
    )
    write_page(
        ctx.repo,
        "decisions/agent-routing",
        """---
title: Agent routing
kind: decision
visibility: internal
status: accepted
---

# Agent routing

## Facts

Current decision state.
""",
    )
    store_claims(
        ctx.ledger,
        "batch-api-apply",
        "inbox/2026-05-10/api-apply",
        [make_claim("services/lore", "Lore exposes consolidation endpoints", source_page_ids=["services/lore"])],
    )
    store_claims(
        ctx.ledger,
        "batch-api-reject",
        "inbox/2026-05-10/api-reject",
        [
            make_claim(
                "decisions/agent-routing",
                "Agent routing must be human approved",
                source_page_ids=["decisions/agent-routing"],
            )
        ],
    )

    app = create_app(ctx.config, mount_workspaces=False)
    with TestClient(app) as client:
        planned = client.post("/api/consolidation/plan", json={"batch_id": "batch-api-apply"})
        assert planned.status_code == 200, planned.text
        [apply_plan] = planned.json()

        listed = client.get("/api/consolidation/plans", params={"status": "pending"})
        assert listed.status_code == 200, listed.text
        assert any(plan["plan_id"] == apply_plan["plan_id"] for plan in listed.json()["plans"])

        preview = client.get(f"/api/consolidation/plans/{apply_plan['plan_id']}")
        assert preview.status_code == 200, preview.text
        assert preview.json()["preview"]["target_page_id"] == "services/lore"

        applied = client.post(f"/api/consolidation/apply/{apply_plan['plan_id']}", json={"force": False})
        assert applied.status_code == 200, applied.text
        assert applied.json()["plan_id"] == apply_plan["plan_id"]

        second_planned = client.post("/api/consolidation/plan", json={"batch_id": "batch-api-reject"})
        assert second_planned.status_code == 200, second_planned.text
        [reject_plan] = second_planned.json()

        rejected = client.post(
            f"/api/consolidation/reject/{reject_plan['plan_id']}",
            json={"reason": "needs review"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"

        rejected_list = client.get("/api/consolidation/plans", params={"status": "rejected"})
        assert rejected_list.status_code == 200, rejected_list.text
        assert any(plan["plan_id"] == reject_plan["plan_id"] for plan in rejected_list.json()["plans"])


def test_update_existing_fact_targets_correct_section(tmp_path, monkeypatch):
    """Replacement only affects the targeted section, not other sections."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = """---
title: Test
kind: service
---

# Test

## Facts

Docker Compose is the deployment tool.

## Details

Docker Compose was originally used for local dev.
"""

    result = _replace_old_object_in_section(
        content,
        "Docker Compose",
        "systemd units are the deployment tool",
        section_title="Facts",
    )
    assert result.status == PatchPlanStatus.ready
    # Should replace in "## Facts" section only
    assert "systemd units are the deployment tool" in result.content
    # Should NOT change the "## Details" section
    assert "Docker Compose was originally used for local dev" in result.content


def test_update_existing_fact_ambiguous_match_needs_manual_review(tmp_path, monkeypatch):
    """Ambiguous or no-section-anchor matches produce needs_manual_review status."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = """---
title: Test
---

# Test

## Facts

Docker Compose is the tool.

## Details

Docker Compose is also mentioned here.
"""

    # No section title provided → needs_manual_review
    result = _replace_old_object_in_section(
        content,
        "Docker Compose",
        "systemd units",
        section_title=None,
    )
    assert result.status == PatchPlanStatus.needs_manual_review
    assert result.reason is not None
    assert "Ambiguous" in result.reason

    # Non-existent section title → needs_manual_review
    result2 = _replace_old_object_in_section(
        content,
        "Docker Compose",
        "systemd units",
        section_title="Nonexistent",
    )
    assert result2.status == PatchPlanStatus.needs_manual_review


def test_update_existing_fact_protects_frontmatter_and_code_blocks(tmp_path, monkeypatch):
    """Frontmatter and code blocks are protected from replacement."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = """---
title: Docker Compose Service
summary: Docker Compose runs the service
---

# Docker Compose Service

## Facts

Docker Compose is the deployment tool.

## Code

```yaml
# Docker Compose configuration
version: "3"
services:
  app:
    image: Docker Compose app
```
"""

    result = _replace_old_object_in_section(
        content,
        "Docker Compose",
        "systemd units",
        section_title="Facts",
    )
    assert result.status == PatchPlanStatus.ready
    # Should replace in Facts section
    assert "systemd units" in result.content
    # Should NOT change frontmatter
    assert "title: Docker Compose Service" in result.content
    assert "summary: Docker Compose runs the service" in result.content
    # Should NOT change code block
    assert "Docker Compose configuration" in result.content
    assert "Docker Compose app" in result.content


def test_replace_in_target_section_skips_other_sections():
    """Replace old_object only within the named section."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = "---\ntitle: Test\n---\n## Summary\nOld fact here.\n\n## Architecture\nOld fact here."
    result = _replace_old_object_in_section(
        content,
        "Old fact here.",
        "New fact.",
        section_title="Summary",
    )

    assert result.status != PatchPlanStatus.needs_manual_review
    assert "New fact." in result.content
    assert result.content.count("Old fact here.") == 1


def test_replace_without_section_returns_manual_review():
    """Without a section title, replacement returns needs_manual_review."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = "---\ntitle: Test\n---\n## Summary\nOld fact here."
    result = _replace_old_object_in_section(
        content,
        "Old fact here.",
        "New fact.",
        section_title=None,
    )

    assert result.status == PatchPlanStatus.needs_manual_review


def test_replace_in_wrong_section_returns_manual_review():
    """If old_object is not in the named section, return needs_manual_review."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = "---\ntitle: Test\n---\n## Summary\nSome text.\n\n## Architecture\nOld fact here."
    result = _replace_old_object_in_section(
        content,
        "Old fact here.",
        "New fact.",
        section_title="Summary",
    )

    assert result.status == PatchPlanStatus.needs_manual_review


def test_replace_skips_code_blocks():
    """Replacement inside a target section skips code blocks."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = "---\ntitle: Test\n---\n## Summary\n```\nOld fact here.\n```\nOld fact here.\n"
    result = _replace_old_object_in_section(
        content,
        "Old fact here.",
        "New fact.",
        section_title="Summary",
    )

    assert result.status != PatchPlanStatus.needs_manual_review
    assert "New fact." in result.content
    assert "Old fact here." in result.content


def test_replace_multiple_matching_sections_returns_manual_review():
    """If section title matches multiple sections, return needs_manual_review."""
    from lore_app.patch_planner import _replace_old_object_in_section
    from lore_app.schemas import PatchPlanStatus

    content = "## Summary\nText 1.\n\n## Summary\nText 2.\n"
    result = _replace_old_object_in_section(
        content,
        "Text 1.",
        "New text.",
        section_title="Summary",
    )

    assert result.status == PatchPlanStatus.needs_manual_review
