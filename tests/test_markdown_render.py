from __future__ import annotations

from lore_app.markdown_render import render_page_markdown
from lore_app.repository import LoreRepository


def test_render_strips_duplicate_title_and_builds_toc(content_dir):
    repo = LoreRepository(content_dir)
    page = repo.read_page("projects/example-project")
    assert page is not None

    rendered = render_page_markdown(page, {"projects/example-project", "services/workflow-engine"})

    assert "<h1" not in rendered.html
    assert rendered.toc[0].id == "services"
    assert rendered.toc[0].title == "Services"


def test_render_rewrites_markdown_and_wiki_links(content_dir):
    repo = LoreRepository(content_dir)
    page = repo.read_page("projects/example-project")
    assert page is not None

    rendered = render_page_markdown(page, {"projects/example-project", "services/workflow-engine"})

    assert 'href="/services/workflow-engine"' in rendered.html
    assert "Workflow Engine" in rendered.html
    assert any(link.page_id == "services/workflow-engine" and link.exists for link in rendered.links)
    assert any(link.page_id == "services/missing" and not link.exists for link in rendered.links)


def test_wiki_link_with_spaces_renders_anchor(tmp_path):
    repo = LoreRepository(tmp_path)
    repo.ensure_root()
    repo.upsert_page(
        "notes/wiki-spaces",
        """---
title: Wiki Spaces
kind: note
visibility: internal
---

# Wiki Spaces

See [[Title With Spaces]] and [[Note (Draft)]].
""",
    )
    page = repo.read_page("notes/wiki-spaces")
    assert page is not None

    rendered = render_page_markdown(page, {"notes/wiki-spaces"})

    # Space/paren targets must render as anchors, not literal markdown.
    assert ">Title With Spaces</a>" in rendered.html
    assert "[Title With Spaces](" not in rendered.html
    assert ">Note (Draft)</a>" in rendered.html
    assert "[Note (Draft)](" not in rendered.html


def test_heading_anchor_aria_label_survives_sanitize(tmp_path):
    repo = LoreRepository(tmp_path)
    repo.ensure_root()
    repo.upsert_page(
        "notes/aria",
        """---
title: Aria
kind: note
visibility: internal
---

# Hello World
""",
    )
    page = repo.read_page("notes/aria")
    assert page is not None

    rendered = render_page_markdown(page, {"notes/aria"})

    assert 'aria-label="Link to this section"' in rendered.html


def test_render_sanitizes_html_and_formats_tables(content_dir):
    repo = LoreRepository(content_dir)
    page = repo.read_page("projects/example-project")
    assert page is not None

    rendered = render_page_markdown(page, {"projects/example-project", "services/workflow-engine"})

    assert '<div class="table-scroll"><table>' in rendered.html
    assert "<script" not in rendered.html
    assert "alert" not in rendered.html
