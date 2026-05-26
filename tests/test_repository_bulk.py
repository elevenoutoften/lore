from __future__ import annotations

from collections.abc import Iterator
from unittest import mock

import pytest

from lore_app.context_graph import build_context_graph
from lore_app.link_graph import build_link_graph
from lore_app.repository import LoreRepository
from lore_app.schemas import PageDetail
from lore_app.search_index import LoreSearchIndex


def _write_page(repo: LoreRepository, page_id: str, *, title: str, kind: str = "project", body: str = "") -> None:
    repo.upsert_page(
        page_id,
        (
            "---\n"
            f"title: {title}\n"
            f"kind: {kind}\n"
            "visibility: internal\n"
            "---\n\n"
            f"# {title}\n\n"
            f"{body or title}\n"
        ),
    )


def test_read_pages_returns_dict_for_existing_ids(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    _write_page(repo, "projects/alpha", title="Alpha")
    _write_page(repo, "projects/bravo", title="Bravo")
    _write_page(repo, "projects/charlie", title="Charlie")

    pages = repo.read_pages(["projects/alpha", "projects/bravo", "projects/charlie"])

    assert set(pages) == {"projects/alpha", "projects/bravo", "projects/charlie"}
    assert all(isinstance(page, PageDetail) for page in pages.values())


def test_read_pages_skips_missing_ids(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    _write_page(repo, "projects/existing", title="Existing")

    pages = repo.read_pages(["projects/existing", "projects/missing"])

    assert set(pages) == {"projects/existing"}


def test_iter_pages_yields_page_details(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    _write_page(repo, "projects/alpha", title="Alpha")
    _write_page(repo, "services/bravo", title="Bravo", kind="service")

    pages = list(repo.iter_pages())

    assert len(pages) == len(repo.list_pages())
    assert all(isinstance(page, PageDetail) for page in pages)


def test_iter_pages_filters_by_kind(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    _write_page(repo, "projects/alpha", title="Alpha")
    _write_page(repo, "services/bravo", title="Bravo", kind="service")
    _write_page(repo, "services/charlie", title="Charlie", kind="service")

    pages = list(repo.iter_pages(kind="service"))

    assert [page.id for page in pages] == ["services/bravo", "services/charlie"]


def test_iter_pages_single_scan_500_pages(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    for index in range(500):
        _write_page(repo, f"projects/page-{index:03d}", title=f"Page {index:03d}", body=f"Content {index}.")

    repo._page_cache = None

    with mock.patch.object(repo, "_read_file", wraps=repo._read_file) as read_file:
        pages = list(repo.iter_pages())

    assert len(pages) == 500
    assert read_file.call_count == 500


def test_list_pages_limit_offset(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    for index in range(10):
        _write_page(repo, f"projects/page-{index:02d}", title=f"Page {index:02d}")

    pages = repo.list_pages(limit=5, offset=2)

    assert [page.id for page in pages] == [
        "projects/page-02",
        "projects/page-03",
        "projects/page-04",
        "projects/page-05",
        "projects/page-06",
    ]


def test_list_pages_with_count_no_double_scan(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    for index in range(50):
        _write_page(repo, f"projects/page-{index:03d}", title=f"Page {index:03d}")

    repo._page_cache = None

    with mock.patch.object(repo, "_scan_pages", wraps=repo._scan_pages) as scan_pages:
        pages, total = repo.list_pages_with_count(limit=10, offset=5)

    assert total == 50
    assert len(pages) == 10
    assert [page.id for page in pages] == [f"projects/page-{index:03d}" for index in range(5, 15)]
    assert scan_pages.call_count == 1


def test_catalog_uses_cache_no_extra_scan(tmp_path, monkeypatch):
    repo = LoreRepository(tmp_path / "pages")
    _write_page(repo, "projects/alpha", title="Alpha", body="alpha")
    _write_page(repo, "services/bravo", title="Bravo", kind="service", body="bravo")

    scan_calls = 0
    original_scan_pages = repo._scan_pages

    def counted_scan_pages():
        nonlocal scan_calls
        scan_calls += 1
        return original_scan_pages()

    monkeypatch.setattr(repo, "_scan_pages", counted_scan_pages)

    repo.list_pages()
    catalog = repo.catalog()

    assert scan_calls == 1
    assert catalog.kinds == ["project", "service"]


def test_search_uses_index_when_available(tmp_path, monkeypatch):
    pages_dir = tmp_path / "pages"
    search_db = tmp_path / "search.db"
    index = LoreSearchIndex(search_db)
    repo = LoreRepository(pages_dir, search_index=index)
    _write_page(repo, "projects/example", title="Example", body="indexed search content")
    repo.list_pages()
    index.rebuild(repo)

    search_calls: list[tuple[str, str | None, int]] = []

    def fake_search(query: str, *, kind: str | None = None, limit: int = 20):
        search_calls.append((query, kind, limit))
        return [
            {
                "page_id": "projects/example",
                "score": 7.5,
                "matched_fields": ["body"],
            }
        ]

    monkeypatch.setattr(index, "search", fake_search)
    monkeypatch.setattr(repo, "_scan_pages", lambda: pytest.fail("search should not scan when index is available"))

    response = repo.search("indexed search", limit=5)

    assert search_calls == [("indexed search", None, 5)]
    assert [hit.page.id for hit in response.hits] == ["projects/example"]
    assert response.hits[0].matches == ["body"]
    index.close()


def test_search_falls_back_to_scan_without_index(tmp_path, monkeypatch):
    repo = LoreRepository(tmp_path / "pages")
    _write_page(repo, "projects/example", title="Example", body="fallback search content")

    scan_calls = 0
    original_scan_pages = repo._scan_pages

    def counted_scan_pages():
        nonlocal scan_calls
        scan_calls += 1
        return original_scan_pages()

    monkeypatch.setattr(repo, "_scan_pages", counted_scan_pages)

    response = repo.search("fallback")

    assert scan_calls == 1
    assert [hit.page.id for hit in response.hits] == ["projects/example"]


def test_context_and_link_graph_use_iter_pages_for_bulk_iteration(tmp_path, monkeypatch):
    repo = LoreRepository(tmp_path / "pages")
    for index in range(60):
        page_id = f"services/service-{index:02d}"
        repo.upsert_page(
            page_id,
            (
                "---\n"
                f"title: Service {index:02d}\n"
                "kind: service\n"
                "visibility: internal\n"
                "sources:\n"
                f"  - docs/service-{index:02d}.md\n"
                "---\n\n"
                f"# Service {index:02d}\n\n"
                f"See [[services/service-{(index + 1) % 60:02d}]].\n"
            ),
        )

    details = list(repo.iter_pages())
    iter_calls = 0

    def fake_iter_pages(*, kind=None, visibility=None, q=None) -> Iterator[PageDetail]:
        nonlocal iter_calls
        iter_calls += 1
        pages = details
        if kind is not None:
            pages = [page for page in pages if page.kind == kind]
        if visibility is not None:
            pages = [page for page in pages if page.visibility == visibility]
        if q is not None:
            needle = q.casefold()
            pages = [page for page in pages if needle in page.id.casefold() or needle in page.title.casefold()]
        return iter(pages)

    monkeypatch.setattr(repo, "iter_pages", fake_iter_pages)
    monkeypatch.setattr(repo, "read_page", lambda page_id: pytest.fail(f"unexpected direct read_page({page_id})"))

    context_graph = build_context_graph(repo)
    link_graph = build_link_graph(repo)

    assert iter_calls == 2
    assert len(context_graph.nodes) >= 60
    assert len(link_graph.pages) == 60
