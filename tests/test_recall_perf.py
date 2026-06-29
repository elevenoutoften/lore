from __future__ import annotations

import gc
import json
import sqlite3
import threading
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import quantiles

from lore_app.ledger import LedgerDB


def _normalize_like_ledger(text: str) -> str:
    return " ".join(text.casefold().split())


def _insert_claims(ledger: LedgerDB, claims: list[dict[str, object]], *, batch_id: str = "bulk") -> None:
    timestamp = "2026-06-01T00:00:00+00:00"
    connection = ledger.connection
    connection.execute(
        """
        INSERT OR REPLACE INTO extraction_batches (
            batch_id, created_at, total_captures, total_entities,
            total_claims, total_edges, total_invalidations, dry_run
        ) VALUES (?, ?, 0, 0, ?, 0, 0, 0)
        """,
        (batch_id, timestamp, len(claims)),
    )
    rows = []
    for index, claim in enumerate(claims):
        subject = str(claim["subject"])
        predicate = str(claim.get("predicate", "states"))
        obj = str(claim["object"])
        strength = float(claim.get("strength", 0.6))
        actor = claim.get("actor")
        status = str(claim.get("status", "active"))
        updated_at = str(claim.get("updated_at", timestamp))
        created_at = str(claim.get("created_at", timestamp))
        content = {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "confidence": str(claim.get("confidence", "high")),
        }
        rows.append(
            (
                str(uuid.uuid4()),
                batch_id,
                "claim",
                status,
                content["confidence"],
                None,
                actor,
                None,
                json.dumps(content),
                str(claim.get("dedupe_hash", f"{batch_id}-hash-{index}")),
                "[]",
                "[]",
                None,
                updated_at,
                None,
                None,
                strength,
                _normalize_like_ledger(subject),
                _normalize_like_ledger(predicate),
                _normalize_like_ledger(obj),
                created_at,
                updated_at,
            )
        )
    connection.executemany(
        """
        INSERT INTO extraction_candidates (
            candidate_id, batch_id, candidate_type, status, confidence, epistemic_status,
            actor, lane, content_json, dedupe_hash, source_capture_ids, source_page_ids,
            target_section, observed_at, valid_from, valid_until, strength,
            normalized_subject, normalized_predicate, normalized_object, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()


def test_recall_query_fetches_wider_pool_before_scoring(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    claims = [
        {"subject": f"irrelevant-{index}", "predicate": "states", "object": "background noise", "strength": 0.58}
        for index in range(700)
    ]
    claims.append({"subject": "needle", "predicate": "states", "object": "memory backend", "strength": 0.57})
    _insert_claims(ledger, claims)

    results = ledger.recall_claims(query="memory backend", limit=5, pool_limit=500, record_access=False)

    assert results[0]["content_json"]["subject"] == "needle"


def test_recall_query_plan_uses_hot_path_indexes(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    connection = ledger.connection

    recall_plan = [
        str(row[3])
        for row in connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT * FROM extraction_candidates
            WHERE candidate_type = 'claim'
              AND status IN ('candidate', 'active')
            ORDER BY strength DESC, updated_at DESC
            LIMIT ?
            """,
            (500,),
        ).fetchall()
    ]
    subject_plan = [
        str(row[3])
        for row in connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT * FROM extraction_candidates
            WHERE candidate_type = 'claim'
              AND status IN ('candidate', 'active')
              AND normalized_subject = ?
            ORDER BY strength DESC, updated_at DESC
            LIMIT ?
            """,
            ("subject-1", 500),
        ).fetchall()
    ]
    dedupe_plan = [
        str(row[3])
        for row in connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT candidate_id, strength, confidence, epistemic_status, source_capture_ids,
                   source_page_ids, valid_from, valid_until
            FROM extraction_candidates
            WHERE dedupe_hash = ? AND candidate_type = 'claim'
              AND status IN ('candidate', 'active')
              AND actor IS ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            ("hash-1", None),
        ).fetchall()
    ]

    assert any("idx_candidates_type_status_strength" in line for line in recall_plan), recall_plan
    assert any("idx_candidates_type_status_subject" in line for line in subject_plan), subject_plan
    assert any("idx_candidates_dedupe_type_status" in line for line in dedupe_plan), dedupe_plan
    assert not any("SCAN extraction_candidates" in line for line in recall_plan), recall_plan
    assert not any("SCAN extraction_candidates" in line for line in subject_plan), subject_plan
    assert not any("SCAN extraction_candidates" in line for line in dedupe_plan), dedupe_plan


def test_recall_p95_latency_stays_under_500ms_with_large_ledger(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    claims = [
        {
            "subject": f"bulk/subject-{index}",
            "predicate": "stores",
            "object": f"memory topic {index}",
            "strength": 0.99 if index == 11999 else 0.6 if index % 2 == 0 else 0.55,
        }
        for index in range(12000)
    ]
    _insert_claims(ledger, claims)

    ledger.recall_claims(query="topic 11999", limit=20, pool_limit=500, record_access=False)
    durations: list[float] = []
    for _ in range(15):
        started = time.perf_counter()
        results = ledger.recall_claims(query="topic 11999", limit=20, pool_limit=500, record_access=False)
        durations.append(time.perf_counter() - started)

    p95 = quantiles(durations, n=20, method="inclusive")[18]
    assert results
    assert p95 < 0.5, durations


def test_parallel_recall_and_access_updates_are_thread_safe(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _insert_claims(
        ledger,
        [
            {
                "subject": f"shared-{index}",
                "predicate": "stores",
                "object": f"shared memory {index}",
                "strength": 0.6,
            }
            for index in range(400)
        ],
    )

    barrier = threading.Barrier(8)
    errors: list[str] = []

    def worker() -> None:
        try:
            barrier.wait()
            for _ in range(20):
                recalled = ledger.recall_claims(
                    query="shared memory",
                    limit=10,
                    pool_limit=100,
                    record_access=True,
                )
                ledger.record_claim_access([str(row["candidate_id"]) for row in recalled[:5]])
        except Exception as exc:  # pragma: no cover - exercised on failure
            errors.append(repr(exc))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker) for _ in range(8)]
        for future in as_completed(futures):
            future.result()

    assert not errors, errors
    assert ledger.recall_claims(query="shared memory", limit=1, record_access=False)[0]["access_count"] > 0


def test_close_releases_all_thread_connections(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()

    n_workers = 4
    # Persistent threads (NOT a ThreadPoolExecutor, whose workers die before
    # close()) so each worker survives to probe its OWN connection from its
    # owning thread after close(). Probing from the owning thread is the only way
    # to distinguish a genuinely closed connection ("Cannot operate on a closed
    # database") from the same-thread guard the pre-fix code raised and swallowed.
    opened = threading.Barrier(n_workers + 1, timeout=10)
    closed = threading.Event()
    probes: dict[int, str] = {}

    def worker(index: int) -> None:
        conn = ledger.connection  # this thread's own per-thread connection
        conn.execute("SELECT 1").fetchone()
        opened.wait()
        closed.wait(timeout=10)
        try:
            conn.execute("SELECT 1").fetchone()
            probes[index] = "usable"
        except sqlite3.ProgrammingError as exc:
            probes[index] = "closed" if "closed database" in str(exc).lower() else f"other:{exc}"

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(n_workers)]
    for thread in threads:
        thread.start()
    opened.wait()  # all worker connections now exist

    main_conn = ledger.connection
    assert len(ledger._all_conns) >= n_workers + 1

    # close() must release every per-thread connection without leaking an
    # unclosed-database ResourceWarning. Drain unrelated garbage first (other
    # tests' un-closed ledgers) so their warnings don't pollute this capture.
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ledger.close()
        gc.collect()
    db_warnings = [
        w for w in caught if issubclass(w.category, ResourceWarning) and "database" in str(w.message).lower()
    ]
    assert not db_warnings, f"close() leaked an unclosed database: {[str(w.message) for w in db_warnings]}"

    closed.set()
    for thread in threads:
        thread.join(timeout=10)

    # Load-bearing: each worker connection is genuinely closed, probed from its
    # owning thread. Pre-fix every worker connection is still "usable" because the
    # foreign-thread close() raised ProgrammingError and was swallowed.
    assert probes == {index: "closed" for index in range(n_workers)}
    assert len(ledger._all_conns) == 0
    assert ledger._connection is None
    try:
        main_conn.execute("SELECT 1")
    except sqlite3.ProgrammingError as exc:
        assert "closed database" in str(exc).lower()
    else:  # pragma: no cover - exercised only on regression
        raise AssertionError("expected the main-thread SQLite connection to be closed")
