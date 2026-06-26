from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from lore_app.ledger import PINNED_CLAIM_DECAY_FLOOR, LedgerDB
from lore_app.schemas import ExtractedClaim, ExtractionResult


def test_apply_decay_affects_candidates_without_last_accessed_at(tmp_path):
    """Decay must work even though last_accessed_at is never written (uses updated_at)."""
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-decay",
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-10/decay"],
            claims=[
                ExtractedClaim(
                    subject="services/decay",
                    predicate="states",
                    object="Decay should reduce strength over time.",
                    confidence="high",
                )
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )

    result = ledger.apply_decay(days_since_access=100)

    assert result.decayed_count == 1
    assert result.max_strength < 0.5


def test_apply_decay_uses_dedicated_anchor_without_refreshing_updated_at(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-decay-anchor",
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-10/decay-anchor"],
            claims=[
                ExtractedClaim(
                    subject="services/decay",
                    predicate="states",
                    object="Decay has its own anchor.",
                    confidence="high",
                )
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    before = ledger.get_active_claims()[0]

    result = ledger.apply_decay(days_since_access=10)

    assert result.decayed_count == 1
    after = ledger.get_active_claims()[0]
    assert after["strength"] < before["strength"]
    assert after["updated_at"] == before["updated_at"]
    assert after["last_decayed_at"] is not None
    assert after["last_accessed_at"] is None


def test_apply_decay_preserves_floor_onset_for_forget_window(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-floor-anchor",
            processed_at="2026-01-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-01-01/floor-anchor"],
            claims=[
                ExtractedClaim(
                    subject="services/lore",
                    predicate="states",
                    object="A floor-aged fact",
                    confidence="low",
                )
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    candidate_id = ledger.get_active_claims()[0]["candidate_id"]
    floor_onset = "2026-01-15T00:00:00+00:00"
    ledger.connection.execute(
        "UPDATE extraction_candidates SET strength = 0.01, last_decayed_at = ? WHERE candidate_id = ?",
        (floor_onset, candidate_id),
    )
    ledger.connection.commit()

    ledger.apply_decay(days_since_access=10)

    assert ledger.get_active_claims()[0]["last_decayed_at"] == floor_onset


def test_operator_declared_high_confidence_claim_survives_decay_archive_and_recall(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    claim = ExtractedClaim(
        subject="identity/operator",
        predicate="declared",
        object="Pinned operator-declared facts must stay recallable.",
        confidence="high",
        epistemic_status="operator_declared",
    )
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-pinned-floor",
            processed_at="2026-01-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-01-01/pinned-floor"],
            claims=[claim],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    candidate_id = ledger.get_active_claims()[0]["candidate_id"]

    result = ledger.apply_decay(days_since_access=10_000)

    assert result.decayed_count == 1
    [row] = ledger.get_active_claims()
    assert row["candidate_id"] == candidate_id
    assert row["decay_floor"] == PINNED_CLAIM_DECAY_FLOOR
    assert row["strength"] == PINNED_CLAIM_DECAY_FLOOR

    future = datetime.now(UTC) + timedelta(days=31)
    assert ledger.archive_floored_claims(30, now=future) == []
    recalled = ledger.recall_claims(
        query="operator-declared facts",
        min_strength=PINNED_CLAIM_DECAY_FLOOR,
        limit=5,
    )
    assert [claim["candidate_id"] for claim in recalled] == [candidate_id]


def test_unpinned_claim_still_decays_to_default_floor_and_archives(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    claim = ExtractedClaim(
        subject="services/lore",
        predicate="forgets",
        object="Ordinary claims still archive at the floor.",
        confidence="low",
    )
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-unpinned-floor",
            processed_at="2026-01-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-01-01/unpinned-floor"],
            claims=[claim],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    candidate_id = ledger.get_active_claims()[0]["candidate_id"]

    ledger.apply_decay(days_since_access=10_000)

    [row] = ledger.get_active_claims()
    assert row["candidate_id"] == candidate_id
    assert row["decay_floor"] is None
    assert row["strength"] == 0.01

    future = datetime.now(UTC) + timedelta(days=31)
    assert ledger.archive_floored_claims(30, now=future) == [candidate_id]
    assert ledger.recall_claims(query="ordinary claims", limit=5) == []
    [archived] = ledger.get_candidates(candidate_type="claim")
    assert archived["status"] == "archived"


def test_deadletter_store_list_and_resolve(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()

    deadletter_id = ledger.store_deadletter(
        capture_id="inbox/2026-05-10/deadletter",
        provider="fallback",
        failure_kind="fallback_exhausted",
        failure_detail="llm=Provider unavailable; fallback=fallback failed",
        payload=json.dumps({"capture_id": "inbox/2026-05-10/deadletter"}),
        batch_id="batch-deadletter",
    )

    unresolved = ledger.list_deadletters(status="unresolved")
    assert len(unresolved) == 1
    assert unresolved[0]["deadletter_id"] == deadletter_id
    assert unresolved[0]["capture_id"] == "inbox/2026-05-10/deadletter"
    assert unresolved[0]["status"] == "unresolved"
    assert unresolved[0]["retry_count"] == 0
    assert unresolved[0]["attempted_at"] is None
    assert unresolved[0]["last_retry_at"] is None

    assert ledger.resolve_deadletter(deadletter_id, resolved_by="llm") is True

    resolved = ledger.list_deadletters(status="resolved")
    assert len(resolved) == 1
    assert resolved[0]["deadletter_id"] == deadletter_id
    assert resolved[0]["resolved_at"] is not None
    assert resolved[0]["resolved_by"] == "llm"
    assert ledger.list_deadletters(status="unresolved") == []


def test_deadletter_increment_retry_updates_metadata(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()

    deadletter_id = ledger.store_deadletter(
        capture_id="inbox/2026-05-10/retry-metadata",
        provider="fallback",
        failure_kind="fallback_exhausted",
        failure_detail="llm=Provider unavailable; fallback=fallback failed",
        payload=json.dumps({"capture_id": "inbox/2026-05-10/retry-metadata"}),
        batch_id="batch-deadletter",
    )

    assert ledger.increment_retry(deadletter_id) is True

    retried = ledger.list_deadletters(status="retried")
    assert len(retried) == 1
    assert retried[0]["deadletter_id"] == deadletter_id
    assert retried[0]["retry_count"] == 1
    assert retried[0]["attempted_at"] is not None
    assert retried[0]["last_retry_at"] is not None

    assert ledger.increment_retry(deadletter_id) is True
    retried_again = ledger.list_deadletters(status="retried")
    assert retried_again[0]["retry_count"] == 2


def test_get_candidates_statuses_filter_excludes_dead_claims(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-filter",
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-10/filter"],
            claims=[
                ExtractedClaim(subject=f"services/s{i}", predicate="states", object=f"fact {i}", confidence="high")
                for i in range(3)
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    ids = [c["candidate_id"] for c in ledger.get_candidates(candidate_type="claim", limit=10)]
    ledger.activate_candidate(ids[0])
    ledger.reject_candidate(ids[1])

    live = {c["candidate_id"] for c in ledger.get_candidates(statuses=("candidate", "active"))}
    assert ids[0] in live  # active
    assert ids[2] in live  # still candidate
    assert ids[1] not in live  # rejected is dead knowledge


def test_get_candidates_max_rows_lifts_default_clamp(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-bulk",
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-10/bulk"],
            claims=[
                ExtractedClaim(subject=f"services/s{i}", predicate="states", object=f"fact {i}", confidence="high")
                for i in range(600)
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    # External callers keep the protective 500-row clamp even with a huge limit.
    assert len(ledger.get_candidates(candidate_type="claim", limit=10000)) == 500
    # Internal graph/RAG callers can scan past it with max_rows.
    uncapped = ledger.get_candidates(
        candidate_type="claim", statuses=("candidate", "active"), limit=600, max_rows=600
    )
    assert len(uncapped) > 500
