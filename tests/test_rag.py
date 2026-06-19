from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import lore_app.rag.hybrid_retrieval as hybrid_module
import lore_app.rag.vector_store as vector_store_module
from lore_app.config import LoreConfig
from lore_app.context_graph import build_context_graph
from lore_app.main import create_app
from lore_app.rag.chunker import chunk_page
from lore_app.rag.eval_retrieval import evaluate_retrieval
from lore_app.rag.hybrid_retrieval import hybrid_retrieve, hybrid_retrieve_expanded
from lore_app.rag.vector_store import VectorStore
from lore_app.repository import LoreRepository
from lore_app.route_utils import index_vectors_for_page, reconcile_vector_index, vector_index_stats
from lore_app.schemas import (
    ContextEdgeType,
    ContextGraph,
    ContextGraphEdge,
    ContextGraphNode,
    ContextNodeType,
    ExtractedClaim,
    ExtractionResult,
)


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers={"Mcp-Method": method},
    )


def test_chunk_page_splits_by_heading_with_overlap():
    body = "# Title\n\nIntro text.\n\n## Services\n\ngateway service and workflow engine run here.\n\n## Ops\n\nGateway routing."

    chunks = chunk_page("projects/example-project", body, body, chunk_size=45, overlap=12)

    assert [chunk["chunk_id"] for chunk in chunks] == [
        "projects/example-project#0",
        "projects/example-project#1",
        "projects/example-project#2",
    ]
    assert chunks[1]["content"].startswith("Intro text.")
    assert "## Services" in chunks[1]["content"]


def test_vector_store_searches_sparse_tfidf(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.upsert_chunk("a#0", "a", 0, "gateway service gateway GPU rendering")
    store.upsert_chunk("b#0", "b", 0, "Task board procedure workflow")
    store.rebuild_doc_freq()

    results = store.search("GPU gateway", limit=2)

    assert results[0]["page_id"] == "a"
    assert results[0]["score"] > 0


def test_vector_store_filters_sparse_hits_by_lane_and_actor(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.upsert_page_chunks(
        "tenant/a",
        [
            {
                "chunk_id": "tenant/a#0",
                "page_id": "tenant/a",
                "chunk_index": 0,
                "content": "shared tenant needle",
                "actor": "agent-a",
                "lane": "project",
            }
        ],
    )
    store.upsert_page_chunks(
        "tenant/b",
        [
            {
                "chunk_id": "tenant/b#0",
                "page_id": "tenant/b",
                "chunk_index": 0,
                "content": "shared tenant needle",
                "actor": "agent-b",
                "lane": "ops",
            }
        ],
    )

    assert [row["page_id"] for row in store.search("tenant needle", actor="agent-a")] == ["tenant/a"]
    assert [row["page_id"] for row in store.search("tenant needle", lane="ops")] == ["tenant/b"]
    assert store.search("tenant needle", actor="agent-a", lane="ops") == []


def test_vector_index_reconciliation_rebuilds_drift_scope_and_clears_pending(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    repo.ensure_root()
    repo.upsert_page(
        "tenant/reconcile",
        """---
title: Reconcile
actor: agent-a
lane: ops
---

# Reconcile

reconcile vector drift needle
""",
    )
    store = VectorStore(tmp_path / "vectors.db")
    store.record_pending_reindex("tenant/reconcile", "test failure")

    before = vector_index_stats(repo, store)
    assert before["page_drift"] == 1
    assert before["pending_reindex"] == 1

    stats = reconcile_vector_index(repo, store)

    assert stats["reconciled"] is True
    assert stats["page_drift"] == 0
    assert stats["pending_reindex"] == 0
    assert store.search("drift needle", actor="agent-a", lane="ops")[0]["page_id"] == "tenant/reconcile"

    store.upsert_chunk("tenant/reconcile#0", "tenant/reconcile", 0, "reconcile vector drift needle")
    scoped_drift = vector_index_stats(repo, store)
    assert scoped_drift["page_drift"] == 0
    assert scoped_drift["scope_drift"] == 1

    stats = reconcile_vector_index(repo, store)

    assert stats["scope_drift"] == 0
    assert store.search("drift needle", actor="agent-a", lane="ops")[0]["page_id"] == "tenant/reconcile"


def test_indexing_failure_surfaces_in_drift_gauge(tmp_path, monkeypatch):
    repo = LoreRepository(tmp_path / "pages")
    repo.ensure_root()
    repo.upsert_page(
        "concepts/drift-test",
        """---
title: Drift Test
actor: agent-a
lane: ops
---

# Drift Test

simulated vector indexing failure
""",
    )
    page = repo.read_page("concepts/drift-test")
    assert page is not None

    store = VectorStore(tmp_path / "vectors.db")

    def raise_indexing_failure(page_id, chunks, *, actor=None, lane=None):
        del chunks, actor, lane
        raise RuntimeError(f"simulated indexing failure for {page_id}")

    monkeypatch.setattr(store, "upsert_page_chunks", raise_indexing_failure)

    with pytest.raises(RuntimeError, match="simulated indexing failure"):
        index_vectors_for_page(store, page)

    assert store.pending_reindex_count() >= 1
    assert vector_index_stats(repo, store)["page_drift"] >= 1


class SynonymEmbeddings:
    model = "test-paraphrase-v1"

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if any(token in lowered for token in ("auth", "login", "token", "failure", "fails")):
                vectors.append([1.0, 0.0, 0.0])
            elif "render" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors

    def close(self):
        pass


class FailingEmbeddings:
    model = "failing-embeddings"

    def embed(self, texts):
        del texts
        raise RuntimeError("embedding endpoint unavailable")

    def close(self):
        pass


def test_vector_store_dense_paraphrase_search_persists_in_sqlite_vec(tmp_path):
    path = tmp_path / "vectors.db"
    store = VectorStore(path)
    if not store.dense_available:
        pytest.skip("sqlite-vec is not available in this environment")
    store.configure_embedding_backend(SynonymEmbeddings())
    store.upsert_page_chunks(
        "auth-runbook",
        [{"chunk_id": "auth#0", "page_id": "auth-runbook", "chunk_index": 0, "content": "login token failure"}],
    )
    store.upsert_page_chunks(
        "rendering",
        [{"chunk_id": "render#0", "page_id": "rendering", "chunk_index": 0, "content": "GPU render queue"}],
    )

    results = store.search("auth fails", limit=2)

    assert results[0]["page_id"] == "auth-runbook"
    assert results[0]["semantic_similarity"] == 1.0
    assert store._conn.execute("SELECT COUNT(*) FROM dense_vectors").fetchone() == (2,)


def test_vector_store_without_embedding_backend_keeps_sparse_fallback(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.upsert_page_chunks(
        "auth-runbook",
        [{"chunk_id": "auth#0", "page_id": "auth-runbook", "chunk_index": 0, "content": "login token failure"}],
    )

    assert store.search("auth fails") == []


def test_create_app_without_sqlite_vec_still_serves_sparse_retrieval(tmp_path, monkeypatch):
    class BrokenSqliteVec:
        @staticmethod
        def load(_connection):
            raise RuntimeError("extension loading disabled")

    monkeypatch.setattr(vector_store_module, "sqlite_vec", BrokenSqliteVec())
    config = LoreConfig()
    config.content_dir = tmp_path / "pages"
    config.search_db = tmp_path / "search.db"
    config.vector_db = tmp_path / "vectors.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.api_keys_db = tmp_path / "api-keys.db"
    config.settings_db = tmp_path / "settings.db"
    app = create_app(config, mount_workspaces=False)

    with TestClient(app) as client:
        store = client.app.state.vector_store
        assert store.dense_available is False
        store.upsert_page_chunks(
            "sparse-page",
            [{"chunk_id": "sparse#0", "page_id": "sparse-page", "chunk_index": 0, "content": "local tfidf recall"}],
        )
        assert client.get("/healthz").status_code == 200
        assert store.search("tfidf")[0]["page_id"] == "sparse-page"


def test_embedding_outage_keeps_sparse_page_index(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.configure_embedding_backend(FailingEmbeddings())

    store.upsert_page_chunks(
        "outage-page",
        [{"chunk_id": "outage#0", "page_id": "outage-page", "chunk_index": 0, "content": "durable sparse index"}],
    )

    assert store.search("durable sparse")[0]["page_id"] == "outage-page"


def test_hybrid_rrf_preserves_fts_only_hit_against_larger_vector_scores():
    class FakeSearch:
        def search(self, query, **kwargs):
            return [{"page_id": "pages/lexical-anchor", "score": 0.000001, "snippet": query}]

    class FakeVector:
        def search(self, query, limit=10):
            return [{"page_id": "pages/vector-distractor", "score": 0.99, "content": query}]

    result = hybrid_retrieve("uniqueprefix", FakeSearch(), FakeVector(), limit=2)

    assert [row["page_id"] for row in result["results"]] == [
        "pages/lexical-anchor",
        "pages/vector-distractor",
    ]
    assert result["results"][0]["sources"] == ["fts"]


def test_hybrid_passes_lane_actor_scope_to_vector_store():
    class FakeSearch:
        def search(self, query, **kwargs):
            return []

    class FakeVector:
        def __init__(self):
            self.kwargs = None

        def search(self, query, **kwargs):
            self.kwargs = kwargs
            return [{"page_id": "pages/scoped", "score": 0.99, "content": query, "actor": "agent-a", "lane": "ops"}]

    vector = FakeVector()
    result = hybrid_retrieve("needle", FakeSearch(), vector, limit=2, lane="ops", actor="agent-a")

    assert vector.kwargs == {"limit": 4, "lane": "ops", "actor": "agent-a"}
    assert [row["page_id"] for row in result["results"]] == ["pages/scoped"]


def test_upsert_page_chunks_batch_insertion(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.upsert_page_chunks(
        "a",
        [
            {"chunk_id": "a#0", "page_id": "a", "chunk_index": 0, "content": "gateway service"},
            {"chunk_id": "a#1", "page_id": "a", "chunk_index": 1, "content": "GPU rendering pipeline"},
        ],
    )

    results = store.search("GPU gateway", limit=2)

    assert results
    assert {result["chunk_id"] for result in results} == {"a#0", "a#1"}


def test_incremental_doc_freq_after_page_update(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.upsert_page_chunks(
        "a",
        [{"chunk_id": "a#0", "page_id": "a", "chunk_index": 0, "content": "gateway service"}],
    )
    store.upsert_page_chunks(
        "b",
        [{"chunk_id": "b#0", "page_id": "b", "chunk_index": 0, "content": "gateway protocol"}],
    )

    assert store._conn.execute(
        "SELECT df FROM doc_freq WHERE token = ?",
        ("gateway",),
    ).fetchone() == (2,)

    store.upsert_page_chunks(
        "a",
        [{"chunk_id": "a#0", "page_id": "a", "chunk_index": 0, "content": "rendering pipeline"}],
    )

    assert store._conn.execute(
        "SELECT df FROM doc_freq WHERE token = ?",
        ("gateway",),
    ).fetchone() == (1,)


def test_search_uses_single_join_query(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.upsert_page_chunks(
        "a",
        [
            {"chunk_id": f"a#{i}", "page_id": "a", "chunk_index": i, "content": f"chunk {i} alpha beta gamma"}
            for i in range(20)
        ],
    )
    store.upsert_page_chunks(
        "b",
        [
            {"chunk_id": f"b#{i}", "page_id": "b", "chunk_index": i, "content": f"chunk {i} alpha delta epsilon"}
            for i in range(20)
        ],
    )

    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        results = store.search("alpha", limit=5)
    finally:
        store._conn.set_trace_callback(None)

    select_count = sum(1 for statement in statements if statement.lstrip().upper().startswith("SELECT"))
    assert select_count <= 3, f"Search used {select_count} SELECT statements: {statements!r}"
    assert results


def test_remove_page_updates_doc_freq_incrementally(tmp_path):
    store = VectorStore(tmp_path / "vectors.db")
    store.upsert_page_chunks(
        "a",
        [{"chunk_id": "a#0", "page_id": "a", "chunk_index": 0, "content": "hello world"}],
    )

    assert store.remove_page("a") == 1

    assert store._conn.execute("SELECT COUNT(*) FROM doc_freq WHERE token IN ('hello', 'world')").fetchone() == (0,)


def test_rag_retrieve_and_evaluate_endpoints(client):
    client.post("/api/search/reindex")

    response = client.post("/api/rag/retrieve", json={"query": "gateway service gateway", "limit": 3})
    assert response.status_code == 200
    payload = response.json()
    capture_result = next(row for row in payload["results"] if row["page_id"] == "procedures/create-lore-capture")
    assert {"vector"} <= set(payload["results"][0]["sources"])
    assert capture_result["title"] == "Capture agent observation"

    evaluation = client.post(
        "/api/rag/evaluate",
        json={
            "queries": [{"query": "gateway service gateway", "relevant_page_ids": ["procedures/create-lore-capture"]}],
            "k": 3,
        },
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["mean_recall"] == 1.0


def test_rag_expanded_result_includes_provenance_fields(client):
    repo = client.app.state.repository
    repo.upsert_page(
        "services/prov-rag",
        """---
title: Prov RAG
kind: service
visibility: internal
observed_at: '2026-06-01T00:00:00+00:00'
actor: agent:rag-bot
source_paths:
  - docs/rag-prov.md
---

RAG content with provenance fields.
""",
    )
    client.post("/api/search/reindex")

    response = client.post("/api/rag/retrieve", json={"query": "provenance", "limit": 5})
    assert response.status_code == 200
    results = response.json()["results"]
    found = [r for r in results if r["page_id"] == "services/prov-rag"]
    assert len(found) >= 1
    result = found[0]
    assert result.get("observed_at") == "2026-06-01T00:00:00+00:00"
    assert result.get("actor") == "agent:rag-bot"
    assert "docs/rag-prov.md" in result.get("source_refs", [])


def test_rag_debug_ui(client):
    client.post("/api/search/reindex")

    response = client.get("/rag", params={"q": "gateway service"})

    assert response.status_code == 200
    assert "RAG Debug" in response.text
    assert "projects/example-project" in response.text


def test_evaluate_retrieval_empty_input():
    result = evaluate_retrieval([], lambda query, limit: {"results": []})

    assert result == {"mean_precision": 0.0, "mean_recall": 0.0, "mean_f1": 0.0, "per_query": []}


def seed_expanded_rag_fixture(client) -> None:
    client.put(
        "/api/pages/projects/routing-alpha",
        json={
            "content": """---
title: Routing Alpha
kind: project
visibility: internal
status: active
---

# Routing Alpha

Alpha routing evidence points at [[decisions/routing-policy]].
""",
        },
    )
    client.put(
        "/api/pages/decisions/routing-policy",
        json={
            "content": """---
title: Routing Policy
kind: decision
visibility: internal
status: accepted
---

# Routing Policy

Canonical traffic policy for service dispatch.
""",
        },
    )
    client.app.state.ledger_db.store_extraction_result(
        ExtractionResult(
            batch_id="batch-expanded-rag",
            processed_at="2026-05-22T00:00:00+00:00",
            source_capture_ids=["inbox/routing-alpha"],
            claims=[
                ExtractedClaim(
                    subject="decisions/routing-policy",
                    predicate="supports",
                    object="service dispatch policy",
                    confidence="high",
                    source_page_ids=["decisions/routing-policy"],
                )
            ],
        )
    )
    client.post("/api/search/reindex")


def test_retrieve_expanded_returns_results_with_relevance_paths(client):
    seed_expanded_rag_fixture(client)

    payload = client.post(
        "/api/rag/retrieve-expanded",
        json={"query": "alpha routing evidence", "limit": 5, "expand_hops": 2},
    ).json()

    decision = next(result for result in payload["results"] if result["page_id"] == "decisions/routing-policy")
    assert decision["relevance_paths"]
    assert decision["relevance_paths"][0]["source_id"] == "projects/routing-alpha"
    assert decision["relevance_paths"][0]["path"][0]["edge_type"] == "mentions"


def test_retrieve_expanded_includes_supporting_claims(client):
    seed_expanded_rag_fixture(client)

    payload = client.post(
        "/api/rag/retrieve-expanded",
        json={"query": "alpha routing evidence", "limit": 5, "expand_hops": 2, "include_claims": True},
    ).json()

    decision = next(result for result in payload["results"] if result["page_id"] == "decisions/routing-policy")
    assert decision["supporting_claims"]


def test_retrieve_expanded_no_expansion_when_hops_zero(client):
    seed_expanded_rag_fixture(client)
    repo = client.app.state.repository
    search_index = client.app.state.search_index
    vector_store = client.app.state.vector_store
    graph = build_context_graph(repo, client.app.state.ledger_db)

    expanded = hybrid_retrieve_expanded(
        "alpha routing evidence", search_index, vector_store, graph, limit=5, expand_hops=0
    )
    base = hybrid_retrieve("alpha routing evidence", search_index, vector_store, None, limit=10)

    assert [result["page_id"] for result in expanded["results"]] == [
        result["page_id"] for result in base["results"][:5]
    ]
    assert all(not result["relevance_paths"] for result in expanded["results"])


def test_retrieve_expanded_builds_adjacency_from_query_scoped_subgraph(monkeypatch):
    class FakeSearch:
        def search(self, query, **kwargs):
            return [{"page_id": "pages/a", "score": 1.0, "snippet": "alpha"}]

    class FakeVector:
        def search(self, query, limit=10):
            return []

    graph = ContextGraph(
        nodes=[
            ContextGraphNode(id="pages/a", type=ContextNodeType.page, label="A"),
            ContextGraphNode(id="pages/b", type=ContextNodeType.page, label="B"),
            ContextGraphNode(id="pages/c", type=ContextNodeType.page, label="C"),
            ContextGraphNode(id="pages/d", type=ContextNodeType.page, label="D"),
        ],
        edges=[
            ContextGraphEdge(source="pages/a", target="pages/b", type=ContextEdgeType.mentions),
            ContextGraphEdge(source="pages/c", target="pages/d", type=ContextEdgeType.mentions),
        ],
    )
    edge_counts = []
    original_adjacency = hybrid_module._context_graph_adjacency

    def counted_adjacency(context_graph, expand_edge_types):
        edge_counts.append(len(context_graph.edges))
        return original_adjacency(context_graph, expand_edge_types)

    monkeypatch.setattr(hybrid_module, "_context_graph_adjacency", counted_adjacency)

    result = hybrid_retrieve_expanded("alpha", FakeSearch(), FakeVector(), graph, limit=5, expand_hops=1)

    assert edge_counts == [1]
    assert any(row["page_id"] == "pages/b" for row in result["results"])


def test_retrieve_expanded_relevant_because_summary(client):
    seed_expanded_rag_fixture(client)

    payload = client.post(
        "/api/rag/retrieve-expanded",
        json={"query": "alpha routing evidence", "limit": 5, "expand_hops": 2, "include_claims": True},
    ).json()

    decision = next(result for result in payload["results"] if result["page_id"] == "decisions/routing-policy")
    assert "reachable via" in decision["relevant_because"]
    assert "supporting claim" in decision["relevant_because"]


def test_retrieve_expanded_without_context_graph(client):
    client.post("/api/search/reindex")

    payload = hybrid_retrieve_expanded(
        "gateway service gateway",
        client.app.state.search_index,
        client.app.state.vector_store,
        None,
        limit=3,
    )

    assert payload["results"]
    assert payload["expansion_stats"] == {"hops": 0, "nodes_visited": 0, "paths_found": 0}
    assert all(not result["relevance_paths"] for result in payload["results"])


def test_mcp_rag_context_expanded(client):
    seed_expanded_rag_fixture(client)

    resp = rpc(
        client,
        "tools/call",
        {
            "name": "lore_rag_context_expanded",
            "arguments": {"query": "alpha routing evidence", "limit": 5, "expand_hops": 2},
        },
    )

    assert resp.status_code == 200
    payload = resp.json()["result"]
    assert payload["isError"] is False
    assert any(result["page_id"] == "decisions/routing-policy" for result in payload["structuredContent"]["results"])


def test_rag_retrieve_returns_expanded_fields(client):
    """The primary /api/rag/retrieve endpoint should return expanded response shape."""
    seed_expanded_rag_fixture(client)

    payload = client.post(
        "/api/rag/retrieve",
        json={"query": "alpha routing evidence", "limit": 5, "expand_hops": 2},
    ).json()

    # Response should be RagExpandedResponse shape
    assert "expansion_stats" in payload
    assert payload["expansion_stats"]["hops"] == 2

    # Results should have expanded fields
    assert payload["results"]
    for result in payload["results"]:
        assert "relevance_paths" in result
        assert "supporting_claims" in result
        assert "contradicting_claims" in result
        assert "related_decisions" in result
        assert "related_traces" in result
        assert "matched_entities" in result
        assert "relevant_because" in result

    # Decision page should be reachable through graph expansion
    decision = next((r for r in payload["results"] if r["page_id"] == "decisions/routing-policy"), None)
    assert decision is not None
    assert decision["relevance_paths"] or decision["relevant_because"]


def test_rag_retrieve_graceful_without_graph(client):
    """Degraded mode: no graph edges, still returns valid results with empty expansion fields."""
    client.post("/api/search/reindex")

    payload = client.post(
        "/api/rag/retrieve",
        json={"query": "gateway service gateway", "limit": 3, "expand_hops": 2},
    ).json()

    assert payload["results"]
    for result in payload["results"]:
        assert "relevance_paths" in result
        assert "matched_entities" in result
        assert isinstance(result["matched_entities"], list)


def test_rag_works_without_manual_reindex(client):
    """The vector index builds on startup, so RAG returns results cold (no reindex)."""
    response = client.post("/api/rag/retrieve", json={"query": "gateway service", "limit": 3})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert any("vector" in (r.get("sources") or []) for r in payload["results"])
