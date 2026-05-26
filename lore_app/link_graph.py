from __future__ import annotations

from collections import Counter

from .markdown_render import render_page_markdown
from .repository import LoreRepository, string_list
from .schemas import EnrichedLinkGraphResponse, GraphNode, LinkEdge, LinkGraphResponse, PageLinks, PageSummary


class LinkGraphCache:
    """In-memory cache for LinkGraphResponse. Invalidated on page writes."""

    def __init__(self):
        self._graph: LinkGraphResponse | None = None
        self._version: int = 0

    def get(self, repo: LoreRepository) -> LinkGraphResponse:
        if self._graph is None:
            self._graph = build_link_graph(repo)
            self._version = _page_fingerprint(repo)
        return self._graph

    def invalidate(self):
        self._graph = None

    def get_if_valid(self, repo: LoreRepository) -> LinkGraphResponse | None:
        if self._graph is not None and self._version == _page_fingerprint(repo):
            return self._graph
        return None


def _page_fingerprint(repo: LoreRepository) -> int:
    """Hash of page IDs + mtimes for cheap change detection."""
    pages = repo.list_pages()
    return hash(tuple((page.id, page.updated_at) for page in pages))


def build_link_graph(repo: LoreRepository) -> LinkGraphResponse:
    pages = repo.list_pages()
    page_by_id = {page.id: page for page in pages}
    page_ids = {page.id for page in pages}
    edges: list[LinkEdge] = []
    seen: set[tuple[str, str | None, str, str | None, bool, bool]] = set()

    for page in repo.iter_pages():
        summary = page_by_id.get(page.id, page)
        rendered = render_page_markdown(page, page_ids)
        for link in rendered.links:
            edge = LinkEdge(
                source=summary.id,
                source_title=summary.title,
                target=link.page_id,
                target_title=page_by_id[link.page_id].title if link.page_id in page_by_id else None,
                href=link.href,
                label=link.label,
                exists=link.exists,
                external=link.external,
            )
            key = (edge.source, edge.target, edge.href, edge.label, edge.exists, edge.external)
            if key in seen:
                continue
            seen.add(key)
            edges.append(edge)

    edges.sort(key=edge_sort_key)
    return LinkGraphResponse(
        pages=pages,
        links=edges,
        broken_links=[edge for edge in edges if not edge.external and not edge.exists],
    )


def build_source_edges(repo: LoreRepository) -> list[LinkEdge]:
    edges: list[LinkEdge] = []
    for page in repo.iter_pages():
        for source in string_list(page.frontmatter.get("sources")):
            edges.append(
                LinkEdge(
                    source=page.id,
                    source_title=page.title,
                    target=source,
                    href=source,
                    label=source,
                    exists=True,
                    relationship_type="source_ref",
                )
            )
    edges.sort(key=edge_sort_key)
    return edges


def build_enriched_graph(
    repo: LoreRepository,
    cache: LinkGraphCache | None = None,
) -> EnrichedLinkGraphResponse:
    base_graph = cache.get(repo) if cache is not None else build_link_graph(repo)
    source_edges = build_source_edges(repo)
    all_links = [*base_graph.links, *source_edges]

    inbound_counts = Counter(
        edge.target
        for edge in base_graph.links
        if edge.exists and not edge.external and edge.target is not None
    )
    outbound_counts = Counter(
        edge.source
        for edge in base_graph.links
        if edge.exists and not edge.external and edge.target is not None
    )
    nodes = [
        GraphNode(
            page_id=page.id,
            title=page.title,
            kind=page.kind,
            visibility=page.visibility,
            tags=page.tags,
            summary=page.summary,
            inbound_count=inbound_counts[page.id],
            outbound_count=outbound_counts[page.id],
        )
        for page in base_graph.pages
    ]

    return EnrichedLinkGraphResponse(
        nodes=nodes,
        links=all_links,
        broken_links=base_graph.broken_links,
    )


def page_links(repo: LoreRepository, page_id: str, graph: LinkGraphResponse | None = None) -> PageLinks | None:
    page = repo.read_page(page_id)
    if page is None:
        return None
    graph = graph or build_link_graph(repo)
    outgoing = [edge for edge in graph.links if edge.source == page.id]
    backlinks = [edge for edge in graph.links if edge.target == page.id and edge.exists]
    missing_links = [edge for edge in outgoing if not edge.external and not edge.exists]
    return PageLinks(page=page_summary(graph.pages, page.id), outgoing=outgoing, backlinks=backlinks, missing_links=missing_links)


def page_summary(pages: list[PageSummary], page_id: str) -> PageSummary:
    for page in pages:
        if page.id == page_id:
            return page
    raise ValueError(f"Page summary missing for {page_id}.")


def edge_sort_key(edge: LinkEdge) -> tuple[str, str, str, str]:
    return (
        edge.source,
        edge.target or "",
        edge.href,
        edge.label or "",
    )
