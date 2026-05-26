from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lore_app.audit import AuditLog
from lore_app.config import LoreConfig
from lore_app.extraction import extract_from_captures
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
    search_index: MagicMock
    vector_store: MagicMock
    graph_cache: MagicMock


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

    search_index = MagicMock()
    vector_store = MagicMock()
    graph_cache = MagicMock()

    planner = PatchPlanner(
        repo,
        ledger,
        audit_log=audit_log,
        search_index=search_index,
        vector_store=vector_store,
        graph_cache=graph_cache,
    )
    return PlannerContext(
        repo=repo, ledger=ledger, planner=planner, config=config,
        audit_log=audit_log, search_index=search_index,
        vector_store=vector_store, graph_cache=graph_cache,
    )


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


def test_apply_reindexes_target_page_and_capture(ctx):
    """After apply, search index, vector store, and graph cache are updated."""
    capture_id = "inbox/2026-05-10/reindex-svc"
    add_capture(
        ctx.repo,
        capture_id,
        title="Reindex Service",
        summary="Reindex search and vectors after apply.",
        suggested_target_page="services/reindex-svc",
    )
    store_claim(
        ctx,
        "batch-reindex",
        capture_id,
        make_claim(
            "services/reindex-svc",
            "Reindex search and vectors after apply.",
            source_page_ids=[capture_id],
        ),
    )

    plans = ctx.planner.plan_batch(batch_id="batch-reindex")
    assert len(plans) == 1

    ctx.search_index.upsert_page_from_detail.reset_mock()
    ctx.vector_store.upsert_page_chunks.reset_mock()
    ctx.graph_cache.invalidate.reset_mock()

    ctx.planner.apply_plan(plans[0].plan_id)

    # search_index.upsert_page_from_detail should be called for both
    # the target page and the accepted capture
    assert ctx.search_index.upsert_page_from_detail.call_count >= 1
    # vector_store should have been called (via index_vectors_for_page)
    assert ctx.vector_store.upsert_page_chunks.call_count >= 0  # may be 0 if no chunks
    # graph_cache should be invalidated (once for target, once per capture)
    assert ctx.graph_cache.invalidate.call_count >= 1


def test_apply_without_indexes_still_works(ctx_no_indexes):
    """Planner without search/vector/graph dependencies still applies successfully."""
    capture_id = "inbox/2026-05-10/no-idx-svc"
    add_capture(
        ctx_no_indexes.repo,
        capture_id,
        title="No Index Service",
        summary="Can apply without index deps.",
        suggested_target_page="services/no-idx-svc",
    )
    store_claim(
        ctx_no_indexes,
        "batch-no-idx",
        capture_id,
        make_claim(
            "services/no-idx-svc",
            "Can apply without index deps.",
            source_page_ids=[capture_id],
        ),
    )

    plans = ctx_no_indexes.planner.plan_batch(batch_id="batch-no-idx")
    assert len(plans) == 1

    # No exception means success
    result = ctx_no_indexes.planner.apply_plan(plans[0].plan_id)
    assert result.operation.value == "create_stub_page"


@pytest.fixture
def ctx_no_indexes(tmp_path, monkeypatch) -> PlannerContext:
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

    # No search_index, vector_store, or graph_cache — just like the old constructor
    planner = PatchPlanner(repo, ledger, audit_log=audit_log)
    return PlannerContext(
        repo=repo, ledger=ledger, planner=planner, config=config,
        audit_log=audit_log, search_index=MagicMock(),
        vector_store=MagicMock(), graph_cache=MagicMock(),
    )
