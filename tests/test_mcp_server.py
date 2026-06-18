from __future__ import annotations

import lore_app.mcp_server as mcp_server


class FakeVectorStore:
    dense_enabled = True

    def __init__(self):
        self.backend = None

    def configure_embedding_backend(self, backend):
        self.backend = backend
        return True

    def semantic_similarities(self, query, texts):
        del query
        return [0.0] * len(texts)


class FakeLedger:
    def __init__(self):
        self.scorer = None

    def configure_semantic_scorer(self, scorer):
        self.scorer = scorer


def test_standalone_mcp_rebuilds_dense_index_when_model_changes(monkeypatch):
    rebuilt: list[tuple[object, object]] = []
    monkeypatch.setattr(mcp_server, "rebuild_vector_index", lambda repo, store: rebuilt.append((repo, store)))
    repo = object()
    store = FakeVectorStore()
    ledger = FakeLedger()
    backend = object()

    mcp_server.configure_dense_retrieval(repo, store, ledger, backend)

    assert store.backend is backend
    assert rebuilt == [(repo, store)]
    assert ledger.scorer == store.semantic_similarities
