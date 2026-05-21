from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from lore_app.config import LoreConfig
from lore_app.extraction import extract_from_captures
from lore_app.ledger import LedgerDB
from lore_app.patch_planner import PatchPlanner
from lore_app.repository import LoreRepository
from lore_app.schemas import ExtractedClaim, ExtractionResult, PatchOperation


@dataclass
class PlannerContext:
    repo: LoreRepository
    ledger: LedgerDB
    planner: PatchPlanner


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> PlannerContext:
    content_dir = tmp_path / "pages"
    monkeypatch.setenv("LORE_CONTENT_DIR", str(content_dir))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vector.db"))

    config = LoreConfig()
    repo = LoreRepository(config.content_dir)
    repo.ensure_root()
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    return PlannerContext(repo=repo, ledger=ledger, planner=PatchPlanner(repo, ledger))


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


def candidate_rows(ledger: LedgerDB, batch_id: str) -> list[dict]:
    rows = ledger.connection.execute(
        """
        SELECT *
        FROM extraction_candidates
        WHERE batch_id = ? AND candidate_type = 'claim'
        ORDER BY candidate_id
        """,
        (batch_id,),
    ).fetchall()
    decoded = []
    for row in rows:
        item = dict(row)
        item["content_json"] = json.loads(item["content_json"])
        item["source_capture_ids"] = json.loads(item["source_capture_ids"])
        item["source_page_ids"] = json.loads(item["source_page_ids"])
        decoded.append(item)
    return decoded


def test_extraction_isolates_source_capture_per_candidate_and_apply_accepts_only_that_capture(ctx):
    capture_a = "inbox/2026-05-10/source-a"
    capture_b = "inbox/2026-05-10/source-b"
    add_capture(
        ctx.repo,
        capture_a,
        title="Source A",
        summary="Source A says Lore has isolated provenance.",
        suggested_target_page="services/source-a",
    )
    add_capture(
        ctx.repo,
        capture_b,
        title="Source B",
        summary="Source B says Workflow Engine has isolated provenance.",
        suggested_target_page="services/source-b",
    )

    result = extract_from_captures(
        ctx.repo,
        capture_ids=[capture_a, capture_b],
        batch_size=2,
        dry_run=False,
        ledger_db=ctx.ledger,
    )
    rows = candidate_rows(ctx.ledger, result.batch_id)
    by_subject = {row["content_json"]["subject"]: row for row in rows}

    assert result.source_capture_ids == [capture_a, capture_b]
    assert by_subject["services/source-a"]["source_page_ids"] == [capture_a]
    assert by_subject["services/source-b"]["source_page_ids"] == [capture_b]

    plan = ctx.planner.plan_batch(candidate_ids=[by_subject["services/source-a"]["candidate_id"]])[0]
    ctx.planner.apply_plan(plan.plan_id)

    assert ctx.repo.read_page(capture_a).frontmatter["status"] == "accepted"
    assert ctx.repo.read_page(capture_b).frontmatter["status"] == "draft"


def test_stub_page_kind_is_inferred_from_target_not_capture_frontmatter(ctx):
    capture_id = "inbox/2026-05-10/new-service"
    add_capture(
        ctx.repo,
        capture_id,
        title="New service capture",
        summary="New service exposes patch consolidation APIs.",
        suggested_target_page="services/new-service",
        kind="capture",
    )
    store_claim(
        ctx,
        "batch-kind",
        capture_id,
        make_claim(
            "services/new-service",
            "New service exposes patch consolidation APIs.",
            source_page_ids=[capture_id],
        ),
    )

    plan = ctx.planner.plan_batch(batch_id="batch-kind")[0]
    assert plan.operation == PatchOperation.create_stub_page
    assert plan.target_page_id == "services/new-service"

    ctx.planner.apply_plan(plan.plan_id)
    page = ctx.repo.read_page("services/new-service")

    assert page is not None
    assert page.frontmatter["kind"] == "service"


def test_planner_skips_capture_targets_but_uses_suggested_canonical_target(ctx):
    capture_without_target = "inbox/2026-05-10/observation"
    add_capture(
        ctx.repo,
        capture_without_target,
        title="Observation",
        summary="Observation should not patch its own capture page.",
    )
    store_claim(
        ctx,
        "batch-capture-target",
        capture_without_target,
        make_claim(
            "inbox/2026-05-10/observation",
            "Observation should not patch its own capture page.",
            source_page_ids=[capture_without_target],
        ),
    )

    assert ctx.planner.plan_batch(batch_id="batch-capture-target") == []

    capture_with_target = "inbox/2026-05-10/lore"
    add_capture(
        ctx.repo,
        capture_with_target,
        title="Lore",
        summary="Lore should patch the service page.",
        suggested_target_page="services/lore",
    )
    store_claim(
        ctx,
        "batch-suggested-target",
        capture_with_target,
        make_claim(
            "inbox/2026-05-10/lore",
            "Lore should patch the service page.",
            source_page_ids=[capture_with_target],
        ),
    )

    plans = ctx.planner.plan_batch(batch_id="batch-suggested-target")

    assert [plan.target_page_id for plan in plans] == ["services/lore"]
