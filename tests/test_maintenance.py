from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from lore_app.config import LoreConfig
from lore_app.main import create_app, run_maintenance_tick
from lore_app.schemas import ExtractedClaim, ExtractionResult


def make_config(tmp_path) -> LoreConfig:
    content_dir = tmp_path / "pages"
    content_dir.mkdir()
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = tmp_path / "search.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.vector_db = tmp_path / "vectors.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.settings_db = tmp_path / "settings.db"
    config.trusted_headers = True
    config.auto_consolidate = False
    # Enabled but with a long interval so the scheduler loop never auto-fires
    # during the test; we drive run_maintenance_tick() directly.
    config.maintenance_enabled = True
    config.maintenance_interval_seconds = 10_000
    return config


def _seed_decayable_claim(app) -> str:
    ledger = app.state.ledger_db
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-maint",
            processed_at="2026-05-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-05-01/maint"],
            claims=[
                ExtractedClaim(
                    subject="services/maintenance",
                    predicate="states",
                    object="Maintenance should decay untouched claims.",
                    confidence="high",
                )
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    cid = ledger.get_active_claims()[0]["candidate_id"]
    # Backdate the claim so apply_decay()'s real-elapsed-time decay engages (a
    # just-created claim is 0 days old and would not decay).
    ledger.connection.execute(
        "UPDATE extraction_candidates SET created_at = ? WHERE candidate_id = ?",
        ("2025-01-01T00:00:00+00:00", cid),
    )
    ledger.connection.commit()
    return cid


def test_maintenance_tick_decays_and_emits_heartbeat(tmp_path):
    config = make_config(tmp_path)
    with TestClient(create_app(config)) as client:
        app = client.app
        cid = _seed_decayable_claim(app)
        before = {c["candidate_id"]: c for c in app.state.ledger_db.get_active_claims()}[cid]

        # A stale page so heartbeat has something to capture.
        stale = client.put(
            "/api/pages/runbooks/maintenance-stale",
            json={
                "content": """---
title: Maintenance Stale
kind: runbook
visibility: internal
summary: A stale runbook for the maintenance tick test.
confidence: high
stale_after: 2020-01-01
---

# Maintenance Stale
""",
            },
        )
        assert stale.status_code == 200, stale.text

        run_maintenance_tick(app)

        after = {c["candidate_id"]: c for c in app.state.ledger_db.get_active_claims()}[cid]
        assert after["strength"] < before["strength"]

        captures = [
            p for p in app.state.repository.list_pages(kind="capture") if p.title.startswith("Heartbeat audit:")
        ]
        assert captures, "maintenance tick should emit at least one heartbeat capture"


def test_memory_health_reports_last_maintenance_at(tmp_path):
    config = make_config(tmp_path)
    with TestClient(create_app(config)) as client:
        first = client.get("/api/memory/health")
        assert first.status_code == 200
        assert first.json()["last_maintenance_at"] is None

        run_maintenance_tick(client.app)

        second = client.get("/api/memory/health")
        assert second.status_code == 200
        assert second.json()["last_maintenance_at"] is not None


def test_maintenance_tick_uses_coalescing_lock(tmp_path):
    config = make_config(tmp_path)
    with TestClient(create_app(config)) as client:
        app = client.app
        lock = app.state.auto_consolidate_lock

        # Hold the existing coalescing lock; the tick must block on it (proving it
        # reuses that lock rather than a second one), then complete once released.
        acquired = lock.acquire()
        assert acquired
        done = threading.Event()

        def run_tick() -> None:
            run_maintenance_tick(app)
            done.set()

        worker = threading.Thread(target=run_tick)
        worker.start()
        try:
            assert not done.wait(0.3), "tick proceeded without acquiring the coalescing lock"
        finally:
            lock.release()
        assert done.wait(5), "tick did not complete after the lock was released"
        worker.join(timeout=5)
        assert app.state.last_maintenance_at is not None
