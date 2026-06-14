"""Recall-latency benchmark over the claim ledger.

Seeds a realistic claim pool and measures end-to-end ``recall_claims`` latency
(p50/p95) so regressions in the recall hot path are visible. Marked ``benchmark``
with a generous ceiling to stay stable across machines and CI.
"""

from __future__ import annotations

import statistics
import time

import pytest

from lore_app.ledger import LedgerDB
from lore_app.schemas import ExtractedClaim, ExtractionResult

CLAIM_COUNT = 500
ITERATIONS = 50
# Generous p95 ceiling: pure in-process scoring over 500 claims is sub-millisecond
# on dev hardware; the wide budget absorbs slow/shared CI without hiding 10x+ regressions.
P95_BUDGET_MS = 250.0


def _seed(ledger: LedgerDB, count: int) -> None:
    claims = [
        ExtractedClaim(
            subject=f"services/svc-{i % 25}",
            predicate="provides",
            object=f"capability {i} for agents and pipelines on the platform",
            confidence="high" if i % 2 == 0 else "medium",
        )
        for i in range(count)
    ]
    # Split into batches so reinforcement and direct inserts both populate the pool.
    for start in range(0, count, 50):
        ledger.store_extraction_result(
            ExtractionResult(
                batch_id=f"bench-{start}",
                processed_at="2026-06-01T00:00:00+00:00",
                source_capture_ids=[f"inbox/2026-06-01/bench-{start}"],
                claims=claims[start : start + 50],
                entities=[],
                edges=[],
                invalidations=[],
            )
        )


@pytest.mark.benchmark
def test_recall_latency_under_budget(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(ledger, CLAIM_COUNT)

    # Warm up (first query primes the connection / caches).
    ledger.recall_claims(query="capability agents platform", limit=20, record_access=False)

    samples: list[float] = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        results = ledger.recall_claims(query="capability agents platform", limit=20, record_access=False)
        samples.append((time.perf_counter() - start) * 1000.0)
        assert results, "recall returned no claims"

    p50 = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    print(f"\nRecall latency over {CLAIM_COUNT} claims: p50={p50:.2f}ms, p95={p95:.2f}ms ({ITERATIONS} iters)")

    assert p95 < P95_BUDGET_MS, f"p95 recall latency {p95:.2f}ms exceeded budget {P95_BUDGET_MS}ms"
