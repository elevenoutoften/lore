"""Tests for recency/salience-weighted recall: scoring, ledger, HTTP, MCP, SDK."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from lore_app.ledger import LedgerDB
from lore_app.recall import (
    compute_recall_score,
    recency_score,
    salience_score,
    text_relevance,
    weights_for_query,
)
from lore_app.schemas import ExtractedClaim, ExtractionResult

# ─── Pure scoring ───────────────────────────────────────────────────────────


def test_recency_score_halves_at_half_life():
    assert recency_score(0) == 1.0
    assert abs(recency_score(30) - 0.5) < 1e-9
    assert abs(recency_score(60) - 0.25) < 1e-9
    assert recency_score(-5) == 1.0  # clamped


def test_salience_score_monotonic_and_saturating():
    assert salience_score(0) == 0.0
    assert salience_score(1) > 0.0
    assert salience_score(5) > salience_score(1)
    assert salience_score(1000) <= 1.0


def test_text_relevance_overlap_fraction():
    assert text_relevance("", "anything") == 1.0
    assert text_relevance("memory backend", "lore is an agent memory backend") == 1.0
    assert text_relevance("memory backend", "pixl uses comfyui") == 0.0
    assert abs(text_relevance("memory backend", "durable memory store") - 0.5) < 1e-9


def test_weights_drop_relevance_without_query_and_keep_with_query():
    with_query = weights_for_query("something")
    assert "relevance" in with_query
    assert abs(sum(with_query.values()) - 1.0) < 1e-6

    without_query = weights_for_query(None)
    assert "relevance" not in without_query
    assert abs(sum(without_query.values()) - 1.0) < 1e-6


def test_compute_recall_score_query_biases_relevant_claim():
    relevant = compute_recall_score(
        strength=0.5, age_days=0, access_count=0, query="memory backend", text="agent memory backend"
    )
    irrelevant = compute_recall_score(
        strength=0.5, age_days=0, access_count=0, query="memory backend", text="comfyui workflow"
    )
    assert relevant.total > irrelevant.total
    assert relevant.relevance == 1.0
    assert irrelevant.relevance == 0.0


# ─── Ledger recall ──────────────────────────────────────────────────────────


def _seed(ledger: LedgerDB, claims: list[ExtractedClaim], *, batch: str = "b", processed_at: str | None = None) -> None:
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id=batch,
            processed_at=processed_at or "2026-06-01T00:00:00+00:00",
            source_capture_ids=[f"inbox/2026-06-01/{batch}"],
            claims=claims,
            entities=[],
            edges=[],
            invalidations=[],
        )
    )


def test_recall_claims_ranks_query_relevance_first(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [
            ExtractedClaim(
                subject="services/pixl", predicate="uses", object="ComfyUI on the GPU box", confidence="high"
            ),
            ExtractedClaim(
                subject="services/lore", predicate="is", object="an agent memory backend", confidence="high"
            ),
        ],
    )

    results = ledger.recall_claims(query="memory backend", limit=5, record_access=False)

    assert results[0]["content_json"]["subject"] == "services/lore"
    assert results[0]["recall_score"] >= results[1]["recall_score"]
    assert results[0]["recall_signals"]["relevance"] == 1.0


def test_recall_records_access_and_populates_salience(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(ledger, [ExtractedClaim(subject="s", predicate="p", object="o", confidence="high")])

    first = ledger.recall_claims(limit=5, record_access=True)
    assert first[0]["access_count"] == 0  # snapshot is pre-increment

    again = ledger.recall_claims(limit=5, record_access=False)
    assert again[0]["access_count"] == 1
    assert again[0]["recall_signals"]["salience"] > 0.0


def test_recall_prefers_stronger_claim_without_query(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(ledger, [ExtractedClaim(subject="weak", predicate="p", object="o", confidence="low")])
    # Reinforce a second claim many times so its strength climbs above the weak one.
    strong = ExtractedClaim(subject="strong", predicate="p", object="o", confidence="high")
    for _ in range(8):
        _seed(ledger, [strong], batch="b2")

    results = ledger.recall_claims(limit=5, record_access=False)
    subjects = [r["content_json"]["subject"] for r in results]
    assert subjects[0] == "strong"


def test_recall_prefers_recent_claim_for_equal_strength(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [ExtractedClaim(subject="old", predicate="p", object="o", confidence="medium")],
        batch="old",
        processed_at="2026-01-01T00:00:00+00:00",
    )
    _seed(
        ledger,
        [ExtractedClaim(subject="new", predicate="p", object="o", confidence="medium")],
        batch="new",
        processed_at="2026-06-01T00:00:00+00:00",
    )

    now = datetime(2026, 6, 2, tzinfo=UTC)
    results = ledger.recall_claims(limit=5, record_access=False, now=now)
    assert results[0]["content_json"]["subject"] == "new"


def test_record_claim_access_is_noop_for_empty_ids(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    assert ledger.record_claim_access([]) == 0


# ─── HTTP surface ───────────────────────────────────────────────────────────


def _reinforce(client, subject: str, predicate: str, obj: str, confidence: str = "high") -> None:
    resp = client.post(
        "/api/ledger/reinforce",
        json={"subject": subject, "predicate": predicate, "object": obj, "confidence": confidence},
    )
    assert resp.status_code == 200, resp.text


def test_memory_recall_endpoint_returns_ranked_claims(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    _reinforce(client, "services/pixl", "uses", "ComfyUI on the GPU box")

    resp = client.get("/api/memory/recall", params={"query": "memory backend", "limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] >= 2
    assert "relevance" in body["weights"]
    assert body["latency_ms"] >= 0.0
    top = body["claims"][0]
    assert top["subject"] == "services/lore"
    assert set(top["recall_signals"]) == {"total", "strength", "recency", "salience", "relevance"}


def test_memory_recall_endpoint_no_query_drops_relevance_weight(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    resp = client.get("/api/memory/recall")
    assert resp.status_code == 200, resp.text
    assert "relevance" not in resp.json()["weights"]


# ─── MCP surface ────────────────────────────────────────────────────────────


def _mcp_call(client, name: str, arguments: dict) -> dict:
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def test_mcp_lore_recall_tool_listed_and_callable(client):
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "lore_recall" in names

    _reinforce(client, "services/lore", "is", "an agent memory backend")
    result = _mcp_call(client, "lore_recall", {"query": "memory backend", "limit": 5})
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["count"] >= 1
    assert structured["claims"][0]["subject"] == "services/lore"


# ─── SDK surface ────────────────────────────────────────────────────────────


def test_sdk_memory_provider_recall_parses_claims(monkeypatch):
    sdk_path = Path(__file__).resolve().parents[1] / "sdk" / "python"
    if str(sdk_path) not in sys.path:
        sys.path.insert(0, str(sdk_path))
    from lore_sdk.memory_provider import MemoryProvider

    provider = MemoryProvider(base_url="http://lore.test", api_key="k")
    captured: dict = {}

    def fake_request(method, path, data=None):
        captured["method"] = method
        captured["path"] = path
        return {"claims": [{"candidate_id": "c1", "subject": "services/lore", "recall_score": 0.9}]}

    monkeypatch.setattr(provider, "_request", fake_request)
    claims = provider.recall("memory backend", limit=3)

    assert captured["method"] == "GET"
    assert captured["path"].startswith("/api/memory/recall?")
    assert "query=memory+backend" in captured["path"]
    assert claims[0]["candidate_id"] == "c1"


# ─── Self-completing loop + self-diagnosing recall ──────────────────────────


def _app_with_auto_consolidate(tmp_path, enabled: bool):
    import os

    os.environ.setdefault("LORE_HOST", "127.0.0.1")

    from lore_app.config import LoreConfig
    from lore_app.main import create_app

    cfg = LoreConfig()
    content = tmp_path / "pages"
    content.mkdir()
    cfg.content_dir = content
    cfg.trusted_headers = True
    cfg.auto_consolidate = enabled
    for attr in ("search_db", "vector_db", "ledger_db", "api_keys_db", "settings_db"):
        setattr(cfg, attr, tmp_path / f"{attr}.db")
    return create_app(cfg)


def test_auto_consolidation_makes_capture_recallable_without_manual_step(tmp_path):
    from fastapi.testclient import TestClient

    app = _app_with_auto_consolidate(tmp_path, enabled=True)
    with TestClient(app) as c:
        # TestClient runs background tasks synchronously, so the capture is
        # consolidated by the time the POST returns.
        r = c.post(
            "/api/memory/capture",
            json={"text": "Pixl renders text as garbage on Illustrious XL.", "agent_name": "nyx"},
        )
        assert r.status_code == 201, r.text

        recall = c.get("/api/memory/recall", params={"query": "illustrious text", "limit": 5})
        assert recall.status_code == 200, recall.text
        body = recall.json()
        assert body["count"] >= 1
        assert body["hint"] is None


def test_recall_hint_flags_pending_captures_when_not_consolidated(tmp_path):
    from fastapi.testclient import TestClient

    app = _app_with_auto_consolidate(tmp_path, enabled=False)
    with TestClient(app) as c:
        r = c.post("/api/memory/capture", json={"text": "An unconsolidated observation.", "agent_name": "nyx"})
        assert r.status_code == 201, r.text

        recall = c.get("/api/memory/recall", params={"query": "unconsolidated", "limit": 5}).json()
        assert recall["count"] == 0
        assert recall["pending_captures"] >= 1
        assert recall["hint"] and "consolidation" in recall["hint"].lower()


# ─── Ledger write-path correctness ──────────────────────────────────────────


def test_supersede_rejects_phantom_new_candidate_and_preserves_old(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(ledger, [ExtractedClaim(subject="s", predicate="p", object="o", confidence="high")])
    old_id = ledger.get_active_claims()[0]["candidate_id"]

    try:
        ledger.supersede_candidate(old_id, "does-not-exist", "bogus")
        raise AssertionError("expected ValueError for phantom new_candidate_id")
    except ValueError as exc:
        assert "not found" in str(exc).lower()

    # The old claim must NOT have been retired by the failed supersede.
    still_live = [c["candidate_id"] for c in ledger.get_active_claims()]
    assert old_id in still_live


def test_ledger_lifecycle_endpoints_return_4xx_not_500(client):
    # Missing candidate -> 404, not 500.
    assert client.post("/api/ledger/archive/missing-zzz").status_code == 404
    assert client.post("/api/ledger/activate/missing-zzz").status_code == 404
    assert client.post("/api/ledger/reject/missing-zzz").status_code == 404
    sup = client.post(
        "/api/ledger/supersede",
        json={"old_candidate_id": "a", "new_candidate_id": "b", "reason": "x"},
    )
    assert sup.status_code == 404

    # Create a real candidate, then an invalid transition -> 409.
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    cand_id = client.get("/api/ledger/claims").json()["claims"][0]["candidate_id"]
    invalid = client.post(f"/api/ledger/archive/{cand_id}")  # candidate -> archived is invalid
    assert invalid.status_code == 409
