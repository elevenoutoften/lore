from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient
from scripts.write_concurrency_benchmark import run_write_concurrency_harness

from lore_app.api_keys import LoreApiKeyStore
from lore_app.config import LoreConfig
from lore_app.ledger import LedgerDB
from lore_app.main import create_app
from lore_app.rag.vector_store import VectorStore
from lore_app.repository import LoreRepository
from lore_app.schemas import (
    ConsolidationRunResult,
    ExtractedClaim,
    ExtractionResult,
    PatchPlan,
    TraceEntry,
)
from lore_app.search_index import LoreSearchIndex


def _extraction_result(i: int) -> ExtractionResult:
    return ExtractionResult(
        batch_id=f"batch-concurrent-{i}",
        processed_at="2026-05-26T00:00:00+00:00",
        source_capture_ids=[f"inbox/concurrent-{i}"],
        claims=[
            ExtractedClaim(
                subject=f"services/lore-{i}",
                predicate="stores",
                object=f"concurrency result {i}",
                confidence="high",
                source_page_ids=[f"inbox/concurrent-{i}"],
            )
        ],
    )


def test_concurrent_ledger_writes(client):
    ledger = client.app.state.ledger_db
    errors: list[str] = []

    def write_candidate(i: int) -> None:
        try:
            ledger.store_extraction_result(_extraction_result(i))
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(write_candidate, i) for i in range(50)]
        for future in as_completed(futures):
            future.result()

    assert not errors, f"OperationalError(s): {errors}"
    assert len(ledger.get_candidates(candidate_type="claim", limit=100)) >= 50


def test_ten_writer_ledger_and_search_write_harness_reports_metrics_without_data_loss(tmp_path):
    metrics = run_write_concurrency_harness(tmp_path, writes=40, max_workers=10)

    assert metrics.requested_writes == 40
    assert metrics.max_workers == 10
    assert metrics.success_count == 40
    assert metrics.failure_count == 0
    assert metrics.failures == []
    assert metrics.ledger_candidates >= 40
    assert metrics.search_hits >= 40
    assert metrics.markdown_pages == 40
    assert metrics.p50_ms > 0
    assert metrics.p99_ms >= metrics.p50_ms


def test_concurrent_api_key_writes(client):
    store = client.app.state.api_key_store
    errors: list[str] = []

    def create_key(i: int) -> None:
        try:
            store.create_key(name=f"test-key-{i}", role="admin")
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(create_key, i) for i in range(20)]
        for future in as_completed(futures):
            future.result()

    assert not errors, f"OperationalError(s): {errors}"
    assert len(store.list_keys()) >= 20


def test_concurrent_search_index_writes(tmp_path):
    search = LoreSearchIndex(tmp_path / "search.db")
    repo = LoreRepository(tmp_path / "pages", search_index=search)
    repo.ensure_root()
    errors: list[str] = []

    for i in range(10):
        detail = repo.upsert_page(
            f"concurrent/page-{i}",
            f"---\ntitle: Page {i}\n---\nContent {i}.\n",
        )
        search.upsert_page_from_detail(detail)

    def write_page(i: int) -> None:
        try:
            detail = repo.upsert_page(
                f"concurrent/page-{i}",
                f"---\ntitle: Page {i}\n---\nContent {i}.\n",
            )
            if i % 2 == 0:
                search.upsert_page_from_detail(detail)
            else:
                page = repo._read_file(repo.page_path(detail.id), detail.id)
                search.upsert_page(page)
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    def remove_page(i: int) -> None:
        try:
            search.remove_page(f"concurrent/page-{i}")
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(write_page, i) for i in range(60)]
        futures.extend(pool.submit(remove_page, i) for i in range(10))
        for future in as_completed(futures):
            future.result()

    assert not errors, f"OperationalError(s): {errors}"
    assert len(search.search("page", limit=100)) >= 50
    search.close()


def test_concurrent_ledger_patch_plans(client):
    ledger = client.app.state.ledger_db
    errors: list[str] = []

    def write_plan(i: int) -> None:
        try:
            plan = PatchPlan(
                plan_id=f"plan-concurrent-{i}",
                trace_id=f"trace-{i}",
                candidate_ids=[],
                target_page_id=f"pages/test-{i}",
                target_section="body",
                operation="insert_new_fact",
                content_diff=f"+ new fact {i}",
                risk_level="low",
                auto_appliable=True,
                policies_applied=[],
                status="pending",
                created_at="2026-05-26T00:00:00+00:00",
            )
            ledger.store_patch_plan(plan, batch_id=f"batch-{i}")
            ledger.update_plan_status(
                f"plan-concurrent-{i}",
                "applied",
                applied_at="2026-05-26T00:00:00+00:00",
            )
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(write_plan, i) for i in range(30)]
        for future in as_completed(futures):
            future.result()

    assert not errors, f"OperationalError(s): {errors}"


def test_concurrent_ledger_consolidation(client):
    ledger = client.app.state.ledger_db
    errors: list[str] = []

    def write_consolidation(i: int) -> None:
        try:
            result = ConsolidationRunResult(
                batch_id=f"cons-batch-{i}",
                captures_processed=1,
                candidates_extracted=1,
                plans_generated=1,
                auto_applied=0,
                review_required=1,
                errors=[],
                dry_run=True,
            )
            ledger.store_consolidation_run(result)
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(write_consolidation, i) for i in range(30)]
        for future in as_completed(futures):
            future.result()

    assert not errors, f"OperationalError(s): {errors}"


def test_concurrent_ledger_traces(client):
    ledger = client.app.state.ledger_db
    errors: list[str] = []

    def write_trace(i: int) -> None:
        try:
            trace = TraceEntry(
                trace_id=f"trace-concurrent-{i}",
                actor=f"agent-{i}",
                reason_summary=f"Concurrent trace {i}",
                related_ids={"page_id": f"pages/test-{i}"},
            )
            ledger.store_trace(trace)
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(write_trace, i) for i in range(30)]
        for future in as_completed(futures):
            future.result()

    assert not errors, f"OperationalError(s): {errors}"


def test_concurrent_ledger_reinforcement(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    for i in range(50):
        ledger.connection.execute(
            """
            INSERT INTO extraction_batches (
                batch_id, created_at, total_captures, total_entities,
                total_claims, total_edges, total_invalidations, dry_run
            ) VALUES (?, ?, 1, 0, 1, 0, 0, 0)
            """,
            (f"batch-concurrent-{i}", "2026-05-26T00:00:00+00:00"),
        )
    ledger.connection.commit()
    errors: list[str] = []

    def reinforce(i: int) -> None:
        try:
            claim = ExtractedClaim(
                subject=f"concurrent/subject-{i}",
                predicate="states",
                object=f"concurrent result {i}",
                confidence="high",
                source_page_ids=[f"pages/test-{i}"],
            )
            ledger.reinforce_or_insert_candidate(
                candidate_type="claim",
                candidate=claim,
                dedupe_hash=f"hash-{i}",
                batch_id=f"batch-concurrent-{i}",
                source_capture_ids=[f"inbox/concurrent-{i}"],
                source_page_ids=[f"pages/test-{i}"],
                metadata={"confidence": "high"},
            )
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(reinforce, i) for i in range(50)]
        for future in as_completed(futures):
            future.result()

    assert not errors, f"OperationalError(s): {errors}"
    assert len(ledger.get_candidates(candidate_type="claim", limit=100)) >= 50
    ledger.close()


def test_shutdown_closes_all_stores(content_dir, search_db, tmp_path):
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = search_db
    config.vector_db = tmp_path / "vectors.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.settings_db = tmp_path / "settings.db"
    app = create_app(config)

    closed: list[str] = []
    for store_attr in ("search_index", "vector_store", "ledger_db", "api_key_store", "settings_store"):
        store = getattr(app.state, store_attr)
        original_close = store.close

        def make_tracker(attr, orig):
            def tracker():
                closed.append(attr)
                return orig()

            return tracker

        store.close = make_tracker(store_attr, original_close)

    # Entering and exiting the TestClient runs the lifespan startup + shutdown.
    with TestClient(app):
        pass

    assert set(closed) == {"search_index", "vector_store", "ledger_db", "api_key_store", "settings_store"}


def test_busy_timeout_set_on_all_stores(tmp_path):
    search = LoreSearchIndex(tmp_path / "s.db")
    vectors = VectorStore(tmp_path / "v.db")
    ledger = LedgerDB(tmp_path / "l.db")
    ledger.initialize()
    keys = LoreApiKeyStore(tmp_path / "k.db")
    keys.initialize()

    for store_name, conn in [
        ("search", search._conn),
        ("vectors", vectors._conn),
        ("ledger", ledger.connection),
        ("keys", keys.connection),
    ]:
        result = conn.execute("PRAGMA busy_timeout").fetchone()
        assert result[0] >= 5000, f"{store_name} busy_timeout = {result[0]}, expected >= 5000"

    search.close()
    vectors.close()
    ledger.close()
    keys.close()


def test_wal_mode_set_on_all_stores(tmp_path):
    search = LoreSearchIndex(tmp_path / "s.db")
    vectors = VectorStore(tmp_path / "v.db")
    ledger = LedgerDB(tmp_path / "l.db")
    ledger.initialize()
    keys = LoreApiKeyStore(tmp_path / "k.db")
    keys.initialize()

    for store_name, conn in [
        ("search", search._conn),
        ("vectors", vectors._conn),
        ("ledger", ledger.connection),
        ("keys", keys.connection),
    ]:
        result = conn.execute("PRAGMA journal_mode").fetchone()
        assert result[0].lower() == "wal", f"{store_name} journal_mode = {result[0]}, expected wal"

    search.close()
    vectors.close()
    ledger.close()
    keys.close()


def test_idempotent_close(tmp_path):
    search = LoreSearchIndex(tmp_path / "s.db")
    vectors = VectorStore(tmp_path / "v.db")
    ledger = LedgerDB(tmp_path / "l.db")
    keys = LoreApiKeyStore(tmp_path / "k.db")

    for store in (search, vectors, ledger, keys):
        store.close()
        store.close()
