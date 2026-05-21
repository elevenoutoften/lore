from __future__ import annotations

from lore_app.rag.chunker import chunk_page
from lore_app.rag.eval_retrieval import evaluate_retrieval
from lore_app.rag.vector_store import VectorStore


def test_chunk_page_splits_by_heading_with_overlap():
    body = "# Title\n\nIntro text.\n\n## Services\n\ngateway service and workflow engine run here.\n\n## Ops\n\nGateway routing."

    chunks = chunk_page("projects/example-project", body, body, chunk_size=45, overlap=12)

    assert [chunk["chunk_id"] for chunk in chunks] == ["projects/example-project#0", "projects/example-project#1", "projects/example-project#2"]
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


def test_rag_retrieve_and_evaluate_endpoints(client):
    client.post("/api/search/reindex")

    response = client.post("/api/rag/retrieve", json={"query": "gateway service gateway", "limit": 3})
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["page_id"] == "procedures/create-lore-capture"
    assert {"vector"} <= set(payload["results"][0]["sources"])
    assert payload["results"][0]["title"] == "Capture agent observation"

    evaluation = client.post(
        "/api/rag/evaluate",
        json={"queries": [{"query": "gateway service gateway", "relevant_page_ids": ["procedures/create-lore-capture"]}], "k": 3},
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["mean_recall"] == 1.0


def test_rag_debug_ui(client):
    client.post("/api/search/reindex")

    response = client.get("/rag", params={"q": "gateway service"})

    assert response.status_code == 200
    assert "RAG Debug" in response.text
    assert "projects/example-project" in response.text


def test_evaluate_retrieval_empty_input():
    result = evaluate_retrieval([], lambda query, limit: {"results": []})

    assert result == {"mean_precision": 0.0, "mean_recall": 0.0, "mean_f1": 0.0, "per_query": []}
