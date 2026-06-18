from __future__ import annotations

import json

from lore_app.ledger import LedgerDB
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
