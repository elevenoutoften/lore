from __future__ import annotations

import importlib

from lore_app import api_keys as api_keys_mod
from lore_app import ledger as ledger_mod
from lore_app import repository as repository_mod
from lore_app import search_index as search_index_mod
from lore_app.rag import vector_store as vector_store_mod


def test_import_main_is_side_effect_free(tmp_path, monkeypatch):
    """Importing lore_app.main should not create directories or SQLite files."""

    def fail(*_args, **_kwargs):
        raise AssertionError("importing lore_app.main should not initialize app resources")

    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vectors.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))
    monkeypatch.setattr(repository_mod.LoreRepository, "__init__", fail)
    monkeypatch.setattr(repository_mod.LoreRepository, "ensure_root", fail)
    monkeypatch.setattr(repository_mod.LoreRepository, "list_pages", fail)
    monkeypatch.setattr(search_index_mod.LoreSearchIndex, "__init__", fail)
    monkeypatch.setattr(vector_store_mod.VectorStore, "__init__", fail)
    monkeypatch.setattr(ledger_mod.LedgerDB, "__init__", fail)
    monkeypatch.setattr(api_keys_mod.LoreApiKeyStore, "__init__", fail)

    import lore_app.main

    importlib.reload(lore_app.main)

    assert not (tmp_path / "pages").exists()
    assert not (tmp_path / "search.db").exists()
    assert not (tmp_path / "vectors.db").exists()
    assert not (tmp_path / "ledger.db").exists()
    assert not (tmp_path / "api_keys.db").exists()
