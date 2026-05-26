from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from lore_app.api_keys import LoreApiKeyStore
from lore_app.ledger import LedgerDB
from lore_app.rag.vector_store import VectorStore
from lore_app.schemas import ExtractedClaim, ExtractionResult
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


def test_shutdown_closes_all_stores(client):
    closed: list[str] = []

    for store_attr in ("search_index", "vector_store", "ledger_db", "api_key_store"):
        store = getattr(client.app.state, store_attr)
        original_close = store.close

        def make_tracker(attr, orig):
            def tracker():
                closed.append(attr)
                return orig()

            return tracker

        store.close = make_tracker(store_attr, original_close)

    for handler in client.app.router.on_shutdown:
        handler()

    assert set(closed) == {"search_index", "vector_store", "ledger_db", "api_key_store"}


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
