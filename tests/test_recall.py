"""Tests for recency/salience-weighted recall: scoring, ledger, HTTP, MCP, SDK."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_app.ledger import LedgerDB
from lore_app.memory_context import _rag_context_line
from lore_app.recall import (
    compute_recall_score,
    recency_score,
    salience_score,
    text_relevance,
    weights_for_query,
)
from lore_app.schemas import ExtractedClaim, ExtractionResult, RagExpandedResult

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
    assert "semantic_similarity" not in weights_for_query(None, semantic_available=True)
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


def test_semantic_similarity_lifts_paraphrase_without_changing_sparse_default():
    sparse = compute_recall_score(
        strength=0.5, age_days=0, access_count=0, query="auth fails", text="login token failure"
    )
    dense = compute_recall_score(
        strength=0.5,
        age_days=0,
        access_count=0,
        query="auth fails",
        text="login token failure",
        semantic_similarity=1.0,
    )

    assert sparse.semantic_similarity == 0.0
    assert dense.semantic_similarity == 1.0
    assert dense.total > sparse.total
    assert "semantic_similarity" in weights_for_query("auth fails", semantic_available=True)


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
    assert len(results) == 1
    assert results[0]["recall_signals"]["relevance"] == 1.0


def test_recall_claims_abstains_when_query_has_no_match(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [ExtractedClaim(subject="services/lore", predicate="is", object="an agent memory backend", confidence="high")],
    )

    results = ledger.recall_claims(
        query="zzzmarsupial quokkanebula anchorless",
        limit=5,
        record_access=False,
    )

    assert results == []


def test_recall_caps_synchronous_semantic_scoring_pool(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [
            ExtractedClaim(subject=f"subject-{index}", predicate="states", object="memory", confidence="high")
            for index in range(80)
        ],
    )
    batch_sizes: list[int] = []

    def semantic_scorer(query: str, texts: list[str]) -> list[float]:
        del query
        batch_sizes.append(len(texts))
        return [0.5] * len(texts)

    ledger.configure_semantic_scorer(semantic_scorer)
    ledger.recall_claims(query="memory", pool_limit=80)

    assert batch_sizes == [50]


def test_recall_records_access_and_populates_salience(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(ledger, [ExtractedClaim(subject="s", predicate="p", object="o", confidence="high")])

    first = ledger.recall_claims(limit=5, record_access=True)
    assert first[0]["access_count"] == 0  # snapshot is pre-increment

    again = ledger.recall_claims(limit=5, record_access=False)
    assert again[0]["access_count"] == 1
    assert again[0]["recall_signals"]["salience"] > 0.0


def test_repeated_recall_is_idempotent_for_recency_and_decay_anchor(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [ExtractedClaim(subject="old", predicate="p", object="o", confidence="high")],
        processed_at="2026-01-01T00:00:00+00:00",
    )

    # Keep the scoring clock well after the insertion wall-clock. This makes
    # recency fractional, so accidentally using last_accessed_at as the anchor
    # jumps recency back to 1.0 and the assertions below fail.
    now = datetime.now(UTC) + timedelta(days=365)
    first = ledger.recall_claims(limit=5, now=now)[0]
    first_recency = first["recall_signals"]["recency"]
    first_age_days = first["age_days"]
    assert 0.0 < first_recency < 1.0
    assert first_age_days > 300

    for _ in range(5):
        repeated = ledger.recall_claims(limit=5, now=now)[0]
        assert repeated["recall_signals"]["recency"] == first_recency
        assert repeated["age_days"] == first_age_days

    row = ledger.get_active_claims()[0]
    assert row["access_count"] == 0
    assert row["last_accessed_at"] is None
    assert row["last_decayed_at"] is None

    ledger.record_claim_access([row["candidate_id"]], now=now.isoformat())
    acknowledged = ledger.recall_claims(limit=5, now=now)[0]
    assert acknowledged["access_count"] == 1
    assert acknowledged["recall_signals"]["salience"] > 0.0
    assert acknowledged["recall_signals"]["recency"] == first_recency
    assert acknowledged["age_days"] == first_age_days
    assert ledger.get_active_claims()[0]["last_decayed_at"] is None


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


def test_recall_prefers_newer_contradiction_and_flags_loser(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [
            ExtractedClaim(
                subject="services/lore",
                predicate="uses",
                object="legacy login",
                confidence="high",
                valid_from="2026-01-01",
            )
        ],
        batch="old",
    )
    _seed(
        ledger,
        [
            ExtractedClaim(
                subject="services/lore",
                predicate="uses",
                object="token login",
                confidence="high",
                valid_from="2026-06-01T07:00:00+07:00",
            )
        ],
        batch="new",
    )

    results = ledger.recall_claims(now=datetime(2026, 6, 10, tzinfo=UTC), limit=5)

    assert results[0]["content_json"]["object"] == "token login"
    assert results[1]["content_json"]["object"] == "legacy login"
    assert results[1]["contradicted_by"] == results[0]["candidate_id"]


def test_recall_explicit_validity_outranks_unknown_contradiction_time(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [
            ExtractedClaim(
                subject="services/lore",
                predicate="uses database",
                object="mysql",
                confidence="high",
            )
        ],
        batch="unknown-validity",
    )
    _seed(
        ledger,
        [
            ExtractedClaim(
                subject="services/lore",
                predicate="uses database",
                object="postgres",
                confidence="high",
                valid_from="2026-06-15",
            )
        ],
        batch="known-validity",
    )

    results = ledger.recall_claims(now=datetime(2026, 6, 18, tzinfo=UTC), limit=5)

    assert results[0]["content_json"]["object"] == "postgres"
    assert results[0].get("contradicted_by") is None
    assert results[1]["content_json"]["object"] == "mysql"
    assert results[1]["contradicted_by"] == results[0]["candidate_id"]


def test_recall_normalizes_mixed_validity_forms_and_excludes_expired_by_default(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [
            ExtractedClaim(
                subject="service",
                predicate="state",
                object="expired",
                confidence="high",
                valid_from="2026-01-01",
                valid_until="2026-06-01",
            ),
            ExtractedClaim(
                subject="service",
                predicate="state",
                object="current",
                confidence="high",
                valid_from="2026-06-01T07:00:00+07:00",
                valid_until="2026-07-01T07:00:00+07:00",
            ),
        ],
    )

    historical = ledger.recall_claims(valid_at="2026-06-01T00:30:00Z", now=datetime(2026, 6, 18, tzinfo=UTC))
    current = ledger.recall_claims(now=datetime(2026, 6, 18, tzinfo=UTC))

    assert [row["content_json"]["object"] for row in historical] == ["current"]
    assert [row["content_json"]["object"] for row in current] == ["current"]
    assert current[0]["valid_from"] == "2026-06-01T00:00:00+00:00"


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
    assert body["count"] == 1
    assert "relevance" in body["weights"]
    assert body["latency_ms"] >= 0.0
    top = body["claims"][0]
    assert top["subject"] == "services/lore"
    assert set(top["recall_signals"]) == {
        "total",
        "strength",
        "recency",
        "salience",
        "relevance",
        "semantic_similarity",
    }


def test_memory_recall_endpoint_no_query_drops_relevance_weight(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    resp = client.get("/api/memory/recall")
    assert resp.status_code == 200, resp.text
    assert "relevance" not in resp.json()["weights"]


def test_memory_recall_endpoint_abstains_for_irrelevant_query(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    resp = client.get("/api/memory/recall", params={"query": "zzzmarsupial quokkanebula anchorless", "limit": 5})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 0
    assert body["claims"] == []
    assert body["hint"] and "no matching memory" in body["hint"].lower()


def test_memory_recall_endpoint_default_does_not_write_access(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    before = client.get("/api/ledger/claims").json()["claims"][0]

    resp = client.get("/api/memory/recall", params={"query": "memory backend"})

    assert resp.status_code == 200, resp.text
    after = client.get("/api/ledger/claims").json()["claims"][0]
    assert after["candidate_id"] == before["candidate_id"]
    assert after["access_count"] == before["access_count"] == 0
    assert after["last_accessed_at"] == before["last_accessed_at"] is None


def test_memory_recall_ack_endpoint_writes_access(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    candidate_id = client.get("/api/ledger/claims").json()["claims"][0]["candidate_id"]

    resp = client.post("/api/memory/recall/ack", json={"candidate_ids": [candidate_id]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["acknowledged_count"] == 1
    after = client.get("/api/ledger/claims").json()["claims"][0]
    assert after["access_count"] == 1
    assert after["last_accessed_at"] is not None


# ─── MCP surface ────────────────────────────────────────────────────────────


def test_memory_context_endpoint_returns_bounded_deterministic_markdown(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    _reinforce(client, "services/pixl", "uses", "ComfyUI on the GPU box")
    before = client.get("/api/ledger/claims").json()["claims"][0]

    params = {
        "query": "memory backend",
        "limit": 5,
        "max_chars": 360,
        "max_tokens": 70,
        "include_rag": False,
    }
    first = client.get("/api/memory/context", params=params)
    second = client.get("/api/memory/context", params=params)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    body = first.json()
    assert body["context"] == second.json()["context"]
    assert body["citations"] == second.json()["citations"]
    assert body["char_count"] == len(body["context"]) <= 360
    assert body["token_count"] <= 70
    assert "[claim:" in body["context"]
    assert body["citations"][0]["kind"] == "claim"
    assert body["citations"][0]["ref"].startswith("claim:")
    assert body["recall"]["count"] == 1
    assert body["rag"] is None

    after = client.get("/api/ledger/claims").json()["claims"][0]
    assert after["candidate_id"] == before["candidate_id"]
    assert after["access_count"] == before["access_count"] == 0
    assert after["last_accessed_at"] == before["last_accessed_at"] is None


def test_rag_context_line_keeps_supporting_claims_out_of_citations():
    result = RagExpandedResult(
        page_id="services/context-source",
        title="Context Source",
        score=0.875,
        sources=["services/context-source"],
        citations=["Prompt context needle lives in this source page."],
        supporting_claims=["c-support-1", "c-support-2"],
        relevant_because="The source page contains the prompt context needle.",
    )

    line, returned_citations = _rag_context_line(result)

    assert "claims c-support-1, c-support-2" in line
    assert "[claim:" not in line
    assert [citation.ref for citation in returned_citations] == [f"page:{result.page_id}"]


def test_memory_context_endpoint_keeps_only_visible_rag_page_citations(client):
    repo = client.app.state.repository
    repo.upsert_page(
        "services/context-source",
        """---
title: Context Source
kind: service
visibility: internal
---

# Context Source

Prompt context needle lives in this source page.
""",
    )
    client.app.state.ledger_db.store_extraction_result(
        ExtractionResult(
            batch_id="batch-memory-context-rag",
            processed_at="2026-06-01T00:00:00+00:00",
            source_capture_ids=["inbox/2026-06-01/context-source"],
            claims=[
                ExtractedClaim(
                    subject="services/context-source",
                    predicate="supports",
                    object="prompt context needle",
                    confidence="high",
                    source_page_ids=["services/context-source"],
                )
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )
    client.post("/api/search/reindex")

    resp = client.get(
        "/api/memory/context",
        params={
            "query": "prompt context needle",
            "include_recall": False,
            "include_rag": True,
            "limit": 5,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    rag_result = next(result for result in body["rag"]["results"] if result["page_id"] == "services/context-source")
    assert "[page:services/context-source]" in body["context"]
    assert "claims " in body["context"]
    assert any(citation["ref"] == "page:services/context-source" for citation in body["citations"])
    assert rag_result["supporting_claims"]
    assert not any(citation["ref"].startswith("claim:") for citation in body["citations"])
    assert body["recall"] is None
    assert body["rag"]["total"] >= 1


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
    after = client.get("/api/ledger/claims").json()["claims"][0]
    assert after["access_count"] == 0
    assert after["last_accessed_at"] is None


def test_mcp_lore_ack_recall_tool_writes_access(client):
    _reinforce(client, "services/lore", "is", "an agent memory backend")
    candidate_id = client.get("/api/ledger/claims").json()["claims"][0]["candidate_id"]

    result = _mcp_call(client, "lore_ack_recall", {"candidate_ids": [candidate_id]})

    assert result["isError"] is False
    assert result["structuredContent"]["acknowledged_count"] == 1
    after = client.get("/api/ledger/claims").json()["claims"][0]
    assert after["access_count"] == 1
    assert after["last_accessed_at"] is not None


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
    assert "record_access=false" in captured["path"]
    assert claims[0]["candidate_id"] == "c1"


# ─── Self-completing loop + self-diagnosing recall ──────────────────────────


def test_sdk_memory_provider_context_and_provider_shapes(monkeypatch):
    sdk_path = Path(__file__).resolve().parents[1] / "sdk" / "python"
    if str(sdk_path) not in sys.path:
        sys.path.insert(0, str(sdk_path))
    from lore_sdk.memory_provider import MemoryProvider

    provider = MemoryProvider(base_url="http://lore.test", api_key="k")
    captured_paths: list[str] = []

    def fake_request(method, path, data=None):
        captured_paths.append(path)
        return {
            "context": "## Lore Memory Context\n- [claim:c1] services/lore is memory.",
            "citations": [{"ref": "claim:c1", "kind": "claim", "id": "c1"}],
            "token_count": 8,
            "char_count": 64,
            "max_tokens": 80,
            "max_chars": 500,
            "truncated": False,
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    context = provider.context("memory backend", max_tokens=80, max_chars=500, include_rag=False)
    openai = provider.to_openai("memory backend", max_tokens=80, max_chars=500)
    anthropic = provider.to_anthropic("memory backend", max_tokens=80, max_chars=500)

    assert context["context"].startswith("## Lore Memory Context")
    assert captured_paths[0].startswith("/api/memory/context?")
    assert "query=memory+backend" in captured_paths[0]
    assert "max_tokens=80" in captured_paths[0]
    assert "max_chars=500" in captured_paths[0]
    assert "include_rag=false" in captured_paths[0]
    assert openai == [{"role": "system", "content": context["context"]}]
    assert anthropic == [{"type": "text", "text": context["context"]}]


def test_sdk_memory_provider_acknowledge_recall_posts_candidate_ids(monkeypatch):
    sdk_path = Path(__file__).resolve().parents[1] / "sdk" / "python"
    if str(sdk_path) not in sys.path:
        sys.path.insert(0, str(sdk_path))
    from lore_sdk.memory_provider import MemoryProvider

    provider = MemoryProvider(base_url="http://lore.test", api_key="k")
    captured: dict = {}

    def fake_request(method, path, data=None):
        captured["method"] = method
        captured["path"] = path
        captured["data"] = data
        return {"acknowledged_count": 1, "timestamp": "2026-06-01T00:00:00+00:00"}

    monkeypatch.setattr(provider, "_request", fake_request)
    result = provider.acknowledge_recall(["c1"])

    assert captured == {
        "method": "POST",
        "path": "/api/memory/recall/ack",
        "data": {"candidate_ids": ["c1"]},
    }
    assert result["acknowledged_count"] == 1


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


def test_rest_capture_auto_consolidates_and_is_recallable(tmp_path):
    """The quickstart REST /api/capture surface must self-complete the loop too."""
    from fastapi.testclient import TestClient

    app = _app_with_auto_consolidate(tmp_path, enabled=True)
    with TestClient(app) as c:
        r = c.post(
            "/api/capture",
            json={
                "observation": "Illustrious XL renders every logo as unreadable garbage.",
                "title": "Illustrious logo limit",
            },
        )
        assert r.status_code == 201, r.text

        status = c.get("/api/consolidation/status").json()
        assert status["total_extracted"] > 0
        assert status["candidates"] > 0
        assert status["candidates_by_status"]["candidate"] > 0

        recall = c.get("/api/memory/recall", params={"query": "illustrious logo", "limit": 5}).json()
        assert recall["count"] >= 1
        assert recall["hint"] is None
        assert any("unreadable garbage" in claim["object"].lower() for claim in recall["claims"])


def test_mcp_capture_auto_consolidates_and_is_recallable(tmp_path):
    """An MCP lore_capture consolidates inline, so it is immediately recallable."""
    from fastapi.testclient import TestClient

    app = _app_with_auto_consolidate(tmp_path, enabled=True)
    with TestClient(app) as c:
        captured = _mcp_call(
            c,
            "lore_capture",
            {"observation": "WAN i2v needs a dedicated style LoRA for the locked look.", "title": "WAN style lora"},
        )
        assert captured["isError"] is False

        status = _mcp_call(c, "lore_consolidation_status", {})["structuredContent"]
        assert status["total_extracted"] > 0
        assert status["candidates"] > 0
        assert status["candidates_by_status"]["candidate"] > 0

        structured = _mcp_call(c, "lore_recall", {"query": "wan style lora", "limit": 5})["structuredContent"]
        assert structured["count"] >= 1
        assert structured["hint"] is None
        assert any("style lora" in claim["object"].lower() for claim in structured["claims"])


def test_mcp_recall_carries_pending_hint_when_unconsolidated(tmp_path):
    from fastapi.testclient import TestClient

    app = _app_with_auto_consolidate(tmp_path, enabled=False)
    with TestClient(app) as c:
        captured = _mcp_call(
            c,
            "lore_capture",
            {"observation": "An unconsolidated MCP observation.", "title": "pending mcp"},
        )
        assert captured["isError"] is False

        structured = _mcp_call(c, "lore_recall", {"query": "no-such-term-zzz", "limit": 5})["structuredContent"]
        assert structured["count"] == 0
        assert structured["pending_captures"] >= 1
        assert structured["hint"] and "consolidation" in structured["hint"].lower()


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


def test_supersede_rejects_self_reference(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(ledger, [ExtractedClaim(subject="s", predicate="p", object="o", confidence="high")])
    cid = ledger.get_active_claims()[0]["candidate_id"]

    try:
        ledger.supersede_candidate(cid, cid, "self")
        raise AssertionError("expected ValueError for self-supersede")
    except ValueError as exc:
        assert "supersede itself" in str(exc).lower()

    # The claim must still be live, not silently retired into a self-referential cycle.
    assert cid in [c["candidate_id"] for c in ledger.get_active_claims()]


def test_supersede_rejects_terminal_old_claim(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(
        ledger,
        [
            ExtractedClaim(subject="s1", predicate="p", object="o1", confidence="high"),
            ExtractedClaim(subject="s2", predicate="p", object="o2", confidence="high"),
        ],
    )
    ids = [c["candidate_id"] for c in ledger.get_active_claims()]
    old_id, new_id = ids[0], ids[1]
    ledger.reject_candidate(old_id, reason="rejected for test")

    try:
        ledger.supersede_candidate(old_id, new_id, "late supersede")
        raise AssertionError("expected ValueError superseding a terminal claim")
    except ValueError as exc:
        assert "cannot be superseded" in str(exc).lower()


def test_candidate_statuses_reports_known_ids(tmp_path):
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    _seed(ledger, [ExtractedClaim(subject="s", predicate="p", object="o", confidence="high")])
    cid = ledger.get_active_claims()[0]["candidate_id"]

    statuses = ledger.candidate_statuses([cid, "missing-id"])
    assert statuses[cid] == "candidate"
    assert "missing-id" not in statuses
    assert ledger.candidate_statuses([]) == {}


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

    # Self-supersede is a 409, never a silent 200 that destroys the claim.
    self_sup = client.post(
        "/api/ledger/supersede",
        json={"old_candidate_id": cand_id, "new_candidate_id": cand_id, "reason": "self"},
    )
    assert self_sup.status_code == 409
    still_live = [c["candidate_id"] for c in client.get("/api/ledger/claims").json()["claims"]]
    assert cand_id in still_live
