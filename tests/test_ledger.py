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

    assert ledger.resolve_deadletter(deadletter_id) is True

    resolved = ledger.list_deadletters(status="resolved")
    assert len(resolved) == 1
    assert resolved[0]["deadletter_id"] == deadletter_id
    assert resolved[0]["resolved_at"] is not None
    assert ledger.list_deadletters(status="unresolved") == []
