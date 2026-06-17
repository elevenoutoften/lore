"""Discriminating paraphrase gate for optional dense retrieval."""

from lore_app.rag.vector_store import VectorStore


class ParaphraseEmbeddings:
    model = "eval-paraphrase-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0]
            if any(word in text.lower() for word in ("auth", "fails", "login", "token", "failure"))
            else [0.0, 1.0]
            for text in texts
        ]

    def close(self) -> None:
        pass


def test_dense_embeddings_clear_paraphrase_recall_at_3_floor_that_sparse_misses(tmp_path):
    chunks = [
        {"chunk_id": "target#0", "page_id": "target", "chunk_index": 0, "content": "login token failure"},
        {"chunk_id": "d1#0", "page_id": "d1", "chunk_index": 0, "content": "render queue saturation"},
        {"chunk_id": "d2#0", "page_id": "d2", "chunk_index": 0, "content": "deployment release process"},
        {"chunk_id": "d3#0", "page_id": "d3", "chunk_index": 0, "content": "database backup schedule"},
    ]
    sparse = VectorStore(tmp_path / "sparse.db")
    dense = VectorStore(tmp_path / "dense.db")
    dense.configure_embedding_backend(ParaphraseEmbeddings())
    for chunk in chunks:
        sparse.upsert_page_chunks(chunk["page_id"], [chunk])
        dense.upsert_page_chunks(chunk["page_id"], [chunk])

    assert "target" not in {row["page_id"] for row in sparse.search("auth fails", limit=3)}
    assert "target" in {row["page_id"] for row in dense.search("auth fails", limit=3)}
