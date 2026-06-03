from __future__ import annotations

import json

from lore_app.ledger import LedgerDB


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
