"""Recall-quality evaluation: score recency/salience-weighted recall ranking.

Seeds a labelled claim corpus into the ledger and measures how well
``LedgerDB.recall_claims`` surfaces the expected claim for each query, using
recall@k and mean reciprocal rank (MRR). This proves the recall ranking quality,
not just that it runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lore_app.ledger import LedgerDB
from lore_app.schemas import ExtractedClaim, ExtractionResult

EVAL_CORPUS = Path(__file__).parent / "recall_corpus.json"


def load_corpus() -> dict[str, Any]:
    return json.loads(EVAL_CORPUS.read_text(encoding="utf-8"))


def seed_ledger(ledger: LedgerDB, claims: list[dict[str, Any]]) -> None:
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="recall-eval",
            processed_at="2026-06-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-06-01/recall-eval"],
            claims=[ExtractedClaim(**claim) for claim in claims],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )


def rank_of_expected(results: list[dict[str, Any]], expected_subject: str) -> int | None:
    for index, row in enumerate(results, start=1):
        content = row.get("content_json") or {}
        if content.get("subject") == expected_subject:
            return index
    return None


def test_recall_ranking_quality(tmp_path):
    corpus = load_corpus()
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    seed_ledger(ledger, corpus["claims"])

    queries = corpus["queries"]
    recall_at_1 = 0
    recall_at_3 = 0
    reciprocal_ranks: list[float] = []

    for entry in queries:
        # record_access=False keeps scoring deterministic across the eval loop.
        results = ledger.recall_claims(query=entry["query"], limit=5, record_access=False)
        rank = rank_of_expected(results, entry["expected"])
        rr = 1.0 / rank if rank else 0.0
        reciprocal_ranks.append(rr)
        if rank == 1:
            recall_at_1 += 1
        if rank is not None and rank <= 3:
            recall_at_3 += 1
        status = "OK" if rank == 1 else ("near" if rank else "MISS")
        print(f"  {status} {entry['query']!r} -> expected {entry['expected']} at rank {rank}")

    total = len(queries)
    r_at_1 = recall_at_1 / total
    r_at_3 = recall_at_3 / total
    mrr = sum(reciprocal_ranks) / total
    print(f"\nRecall eval: recall@1={r_at_1:.0%}, recall@3={r_at_3:.0%}, MRR={mrr:.3f}")

    assert r_at_3 >= 0.85, f"recall@3 too low: {r_at_3:.0%}"
    assert mrr >= 0.75, f"MRR too low: {mrr:.3f}"


def test_recall_strength_breaks_ties_on_equal_relevance(tmp_path):
    """When two claims match a query equally, the stronger one ranks first."""
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    weak = {"subject": "services/lore", "predicate": "is", "object": "a memory backend", "confidence": "low"}
    strong = {"subject": "services/lore-core", "predicate": "is", "object": "a memory backend", "confidence": "high"}
    seed_ledger(ledger, [weak])
    # Reinforce the strong claim repeatedly to raise its ledger strength.
    for _ in range(8):
        ledger.store_extraction_result(
            ExtractionResult(
                batch_id="strong",
                processed_at="2026-06-01T00:00:00+00:00",
                source_capture_ids=["inbox/2026-06-01/strong"],
                claims=[ExtractedClaim(**strong)],
                entities=[],
                edges=[],
                invalidations=[],
            )
        )

    results = ledger.recall_claims(query="memory backend", limit=5, record_access=False)
    subjects = [(r.get("content_json") or {}).get("subject") for r in results]
    assert subjects[0] == "services/lore-core"
