"""End-to-end hybrid retrieval and recall eval over a LOCOMO-shaped corpus.

This gate materializes a multi-session capture fixture, seeds the ledger with
claims and supersession pairs, and then scores the two production retrieval
surfaces we care about:

- ``POST /api/rag/retrieve`` -> ``hybrid_retrieve_expanded``
- ``LedgerDB.recall_claims``

The corpus carries explicit case labels and expectation text so CI output makes
it obvious when a paraphrase, graph-expansion, abstention, scope-isolation, or
contradiction-update case regresses.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("LORE_HOST", "127.0.0.1")

from lore_app.config import LoreConfig
from lore_app.main import create_app
from lore_app.schemas import ExtractedClaim, ExtractionResult

if TYPE_CHECKING:
    from lore_app.ledger import LedgerDB

EVAL_CORPUS = Path(__file__).parent / "hybrid_corpus.json"
REQUIRED_CASE_TYPES = {
    "abstention",
    "actor-isolation",
    "lane-isolation",
    "recency-discrimination",
    "contradiction-update",
}


def load_corpus() -> dict[str, Any]:
    return json.loads(EVAL_CORPUS.read_text(encoding="utf-8"))


def total_query_count(corpus: dict[str, Any]) -> int:
    return len(corpus["hybrid_queries"]) + len(corpus["recall_queries"])


def corpus_case_types(corpus: dict[str, Any]) -> set[str]:
    return {
        str(entry["case_type"])
        for section in ("hybrid_queries", "recall_queries")
        for entry in corpus[section]
    }


def parse_eval_now(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_abstention_case(entry: dict[str, Any]) -> bool:
    return bool(entry.get("expect_no_results"))


def render_capture_markdown(capture: dict[str, Any]) -> str:
    related = "\n".join(f"  - {page_id}" for page_id in capture.get("related_pages", []))
    frontmatter = [
        "---",
        f"title: {capture['title']}",
        "kind: capture",
        "visibility: internal",
        "status: draft",
        f"observed_at: '{capture['observed_at']}'",
        f"actor: {capture['actor']}",
        f"lane: {capture['lane']}",
        f"session_id: {capture['session_id']}",
    ]
    if related:
        frontmatter.append("related_pages:")
        frontmatter.append(related)
    frontmatter.extend(["---", "", f"# {capture['title']}", "", capture["body"], ""])
    return "\n".join(frontmatter)


def write_corpus_pages(content_dir: Path, corpus: dict[str, Any]) -> None:
    for page in corpus["pages"]:
        path = content_dir / f"{page['id']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page["content"], encoding="utf-8")

    for capture in corpus["captures"]:
        path = content_dir / f"{capture['id']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_capture_markdown(capture), encoding="utf-8")


def find_claim_candidate_id(ledger: LedgerDB, capture_id: str, claim: dict[str, Any]) -> str:
    rows = ledger.connection.execute(
        """
        SELECT candidate_id, content_json, source_capture_ids
        FROM extraction_candidates
        WHERE candidate_type = 'claim'
        ORDER BY created_at DESC
        """
    ).fetchall()
    for row in rows:
        content = json.loads(str(row["content_json"]))
        source_capture_ids = json.loads(str(row["source_capture_ids"]))
        if capture_id not in source_capture_ids:
            continue
        if (
            content.get("subject") == claim["subject"]
            and content.get("predicate") == claim["predicate"]
            and content.get("object") == claim["object"]
        ):
            return str(row["candidate_id"])
    raise AssertionError(f"Could not find seeded candidate for {capture_id} / {claim['id']}")


def seed_claims(ledger: LedgerDB, corpus: dict[str, Any]) -> dict[str, str]:
    claim_id_map: dict[str, str] = {}

    for index, capture in enumerate(corpus["captures"], start=1):
        claims = capture.get("claims", [])
        if not claims:
            continue
        extraction = ExtractionResult(
            batch_id=f"hybrid-eval-{index:02d}",
            processed_at=capture["observed_at"],
            source_capture_ids=[capture["id"]],
            claims=[
                ExtractedClaim(
                    subject=claim["subject"],
                    predicate=claim["predicate"],
                    object=claim["object"],
                    confidence=claim["confidence"],
                    actor=capture["actor"],
                    lane=capture["lane"],
                    observed_at=capture["observed_at"],
                    valid_from=capture["observed_at"],
                    source_page_ids=list(claim.get("source_page_ids", [])),
                )
                for claim in claims
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
        ledger.store_extraction_result(extraction)
        for claim in claims:
            claim_id_map[claim["id"]] = find_claim_candidate_id(ledger, capture["id"], claim)

    for pair in corpus.get("supersessions", []):
        ledger.supersede_candidate(
            claim_id_map[pair["old_claim_id"]],
            claim_id_map[pair["new_claim_id"]],
            pair["reason"],
        )

    return claim_id_map


def first_expected_page_rank(results: list[dict[str, Any]], expected_page_ids: list[str]) -> int | None:
    expected = set(expected_page_ids)
    for index, row in enumerate(results, start=1):
        if row.get("page_id") in expected:
            return index
    return None


def count_expected_page_hits(results: list[dict[str, Any]], expected_page_ids: list[str], k: int = 3) -> int:
    expected = set(expected_page_ids)
    return sum(1 for row in results[:k] if row.get("page_id") in expected)


def first_expected_claim_rank(results: list[dict[str, Any]], expected_candidate_ids: list[str]) -> int | None:
    expected = set(expected_candidate_ids)
    for index, row in enumerate(results, start=1):
        if str(row.get("candidate_id") or "") in expected:
            return index
    return None


def claim_rank(results: list[dict[str, Any]], candidate_id: str) -> int | None:
    for index, row in enumerate(results, start=1):
        if str(row.get("candidate_id") or "") == candidate_id:
            return index
    return None


def count_expected_claim_hits(results: list[dict[str, Any]], expected_candidate_ids: list[str], k: int = 3) -> int:
    expected = set(expected_candidate_ids)
    return sum(1 for row in results[:k] if str(row.get("candidate_id") or "") in expected)


def find_page_result(results: list[dict[str, Any]], page_id: str) -> dict[str, Any] | None:
    for row in results:
        if row.get("page_id") == page_id:
            return row
    return None


def test_hybrid_eval_corpus_shape() -> None:
    corpus = load_corpus()
    total = total_query_count(corpus)
    assert 25 <= total <= 40, f"expected 25-40 eval queries, found {total}"
    assert corpus_case_types(corpus) >= REQUIRED_CASE_TYPES


@pytest.fixture()
def hybrid_eval_runtime(tmp_path: Path) -> dict[str, Any]:
    corpus = load_corpus()
    content_dir = tmp_path / "pages"
    content_dir.mkdir()
    write_corpus_pages(content_dir, corpus)

    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = tmp_path / "search.db"
    config.vector_db = tmp_path / "vectors.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.settings_db = tmp_path / "settings.db"
    config.auto_consolidate = False
    config.trusted_headers = True
    config.code_ingest_roots = [tmp_path]

    app = create_app(config)
    with TestClient(app) as client:
        reindex = client.post("/api/search/reindex")
        assert reindex.status_code == 200, reindex.text
        claim_id_map = seed_claims(client.app.state.ledger_db, corpus)
        yield {
            "client": client,
            "ledger": client.app.state.ledger_db,
            "corpus": corpus,
            "claim_id_map": claim_id_map,
        }


def test_hybrid_retrieve_expanded_eval_gate(hybrid_eval_runtime: dict[str, Any]) -> None:
    client: TestClient = hybrid_eval_runtime["client"]
    corpus = hybrid_eval_runtime["corpus"]
    floors = corpus["floors"]["hybrid"]

    queries = corpus["hybrid_queries"]
    recall_hits = 0
    reciprocal_ranks: list[float] = []
    precision_sum = 0.0
    relevance_path_failures: list[str] = []

    print("\nHybrid eval:")
    for entry in queries:
        request_body = {
            "query": entry["query"],
            "limit": entry.get("limit", 6),
            "expand_hops": entry.get("expand_hops", 2),
            "include_claims": True,
            "include_decisions": True,
        }
        if entry.get("lane"):
            request_body["lane"] = entry["lane"]
        if entry.get("actor"):
            request_body["actor"] = entry["actor"]

        response = client.post("/api/rag/retrieve", json=request_body)
        assert response.status_code == 200, response.text
        payload = response.json()
        results = payload["results"]

        if is_abstention_case(entry):
            abstained = results == []
            reciprocal_ranks.append(1.0 if abstained else 0.0)
            precision_sum += 1.0 if abstained else 0.0
            if abstained:
                recall_hits += 1
            status = "OK" if abstained else "MISS"
            print(
                f"  {status} [{entry['case_type']}] {entry['id']}: "
                f"results={len(results)} :: {entry['expectation']}"
            )
            continue

        rank = first_expected_page_rank(results, entry["expected_page_ids"])
        hits = count_expected_page_hits(results, entry["expected_page_ids"], k=3)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        precision_sum += hits / min(3, len(entry["expected_page_ids"]))
        if rank is not None and rank <= 3:
            recall_hits += 1

        required_page = entry.get("require_relevance_path_page")
        if required_page:
            row = find_page_result(results, required_page)
            result_ids = [result.get("page_id") for result in results]
            if row is None:
                relevance_path_failures.append(f"{entry['id']} missing required page {required_page}; got {result_ids}")
            elif not row.get("relevance_paths"):
                relevance_path_failures.append(f"{entry['id']} expected relevance path for {required_page}")

        status = "OK" if rank and rank <= 3 else "MISS"
        print(
            f"  {status} [{entry['case_type']}] {entry['id']}: "
            f"rank={rank}, hits@3={hits}, expected={entry['expected_page_ids']} :: {entry['expectation']}"
        )

    total = len(queries)
    recall_at_3 = recall_hits / total
    mrr = sum(reciprocal_ranks) / total
    precision_at_3 = precision_sum / total
    print(f"Hybrid gate: recall@3={recall_at_3:.0%}, MRR={mrr:.3f}, precision@3={precision_at_3:.3f}")

    assert recall_at_3 >= floors["recall_at_3"], f"hybrid recall@3 too low: {recall_at_3:.0%}"
    assert mrr >= floors["mrr"], f"hybrid MRR too low: {mrr:.3f}"
    assert precision_at_3 >= floors["precision_at_3"], f"hybrid precision@3 too low: {precision_at_3:.3f}"
    assert not relevance_path_failures, "\n".join(relevance_path_failures)


def test_recall_claims_eval_gate(hybrid_eval_runtime: dict[str, Any]) -> None:
    ledger: LedgerDB = hybrid_eval_runtime["ledger"]
    corpus = hybrid_eval_runtime["corpus"]
    claim_id_map = hybrid_eval_runtime["claim_id_map"]
    floors = corpus["floors"]["recall"]

    queries = corpus["recall_queries"]
    recall_hits = 0
    reciprocal_ranks: list[float] = []
    precision_sum = 0.0
    forbidden_claim_failures: list[str] = []
    recency_failures: list[str] = []

    print("\nRecall eval:")
    for entry in queries:
        expected_candidate_ids = [claim_id_map[claim_id] for claim_id in entry["expected_claim_ids"]]
        forbidden_candidate_ids = [claim_id_map[claim_id] for claim_id in entry.get("forbidden_claim_ids", [])]
        results = ledger.recall_claims(
            query=entry["query"],
            limit=5,
            lane=entry.get("lane"),
            actor=entry.get("actor"),
            record_access=False,
            now=parse_eval_now(entry.get("now")),
        )

        if is_abstention_case(entry):
            abstained = results == []
            reciprocal_ranks.append(1.0 if abstained else 0.0)
            precision_sum += 1.0 if abstained else 0.0
            if abstained:
                recall_hits += 1
            status = "OK" if abstained else "MISS"
            print(
                f"  {status} [{entry['case_type']}] {entry['id']}: "
                f"results={len(results)} :: {entry['expectation']}"
            )
            continue

        rank = first_expected_claim_rank(results, expected_candidate_ids)
        hits = count_expected_claim_hits(results, expected_candidate_ids, k=3)
        top3_ids = [str(row.get("candidate_id") or "") for row in results[:3]]

        for forbidden_id in forbidden_candidate_ids:
            if forbidden_id in top3_ids:
                forbidden_claim_failures.append(
                    f"{entry['id']} expected superseded claim {forbidden_id} to stay out of the top 3"
                )

        if entry["case_type"] == "recency-discrimination":
            newer_id = claim_id_map["claim-memory-audit-slot-new"]
            older_id = claim_id_map["claim-memory-audit-slot-old"]
            newer_rank = claim_rank(results, newer_id)
            older_rank = claim_rank(results, older_id)
            if newer_rank is None or older_rank is None or not newer_rank < older_rank:
                recency_failures.append(
                    f"{entry['id']} expected newer claim {newer_id} to outrank older claim {older_id}; "
                    f"got newer_rank={newer_rank}, older_rank={older_rank}"
                )

        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        precision_sum += hits / min(3, len(expected_candidate_ids))
        if rank is not None and rank <= 3:
            recall_hits += 1

        status = "OK" if rank and rank <= 3 else "MISS"
        print(
            f"  {status} [{entry['case_type']}] {entry['id']}: "
            f"rank={rank}, hits@3={hits}, expected={entry['expected_claim_ids']} :: {entry['expectation']}"
        )

    total = len(queries)
    recall_at_3 = recall_hits / total
    mrr = sum(reciprocal_ranks) / total
    precision_at_3 = precision_sum / total
    print(f"Recall gate: recall@3={recall_at_3:.0%}, MRR={mrr:.3f}, precision@3={precision_at_3:.3f}")

    assert recall_at_3 >= floors["recall_at_3"], f"recall recall@3 too low: {recall_at_3:.0%}"
    assert mrr >= floors["mrr"], f"recall MRR too low: {mrr:.3f}"
    assert precision_at_3 >= floors["precision_at_3"], f"recall precision@3 too low: {precision_at_3:.3f}"
    assert not forbidden_claim_failures, "\n".join(forbidden_claim_failures)
    assert not recency_failures, "\n".join(recency_failures)
