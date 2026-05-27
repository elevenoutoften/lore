from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from .frontmatter import parse_frontmatter
from .schemas import CatalogResponse, EpistemicStatus, PageDetail, PageSummary, SearchHit, SearchResponse

if TYPE_CHECKING:
    from .search_index import LoreSearchIndex

PAGE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class InvalidPageId(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownPage:
    id: str
    path: Path
    content: str
    body: str
    frontmatter: dict[str, Any]
    updated_at: str
    size: int

    def summary(self) -> PageSummary:
        frontmatter = self.frontmatter
        return PageSummary(
            id=self.id,
            title=page_title(self.id, self.body, frontmatter),
            kind=str(frontmatter.get("kind") or infer_kind(self.id)),
            visibility=str(frontmatter.get("visibility") or "internal"),
            status=optional_string(frontmatter.get("status")),
            summary=optional_string(frontmatter.get("summary")),
            tags=string_list(frontmatter.get("tags")),
            sources=string_list(frontmatter.get("sources")),
            source_task=optional_string(frontmatter.get("source_task")),
            decision_id=optional_string(frontmatter.get("decision_id")),
            trace_id=optional_string(frontmatter.get("trace_id")),
            tool_calls=dict_list(frontmatter.get("tool_calls")),
            constraints=string_list(frontmatter.get("constraints")),
            policies_applied=string_list(frontmatter.get("policies_applied")),
            epistemic_status=epistemic_status_value(frontmatter.get("epistemic_status")),
            updated_at=self.updated_at,
            size=self.size,
        )

    def detail(self) -> PageDetail:
        summary = self.summary()
        return PageDetail(
            **summary.model_dump(),
            content=self.content,
            body=self.body,
            frontmatter=self.frontmatter,
        )


class LoreRepository:
    def __init__(self, root: str | Path, search_index: "LoreSearchIndex | None" = None):
        self.root = Path(root).resolve()
        self._page_cache: list[PageSummary] | None = None
        self._meta_cache: list[PageSummary] | None = None
        self._search_index = search_index

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def list_pages(
        self,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[PageSummary]:
        pages, _total = self.list_pages_with_count(
            kind=kind,
            visibility=visibility,
            q=q,
            limit=limit,
            offset=offset,
        )
        return pages

    def list_pages_meta(
        self,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[PageSummary]:
        """Return page summaries without retaining body content in memory."""
        pages, _total = self.list_pages_meta_with_count(
            kind=kind,
            visibility=visibility,
            q=q,
            limit=limit,
            offset=offset,
        )
        return pages

    def list_pages_with_count(
        self,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[PageSummary], int]:
        """Return the current page slice and total filtered count in one cache pass."""
        if self._page_cache is None:
            self._page_cache = [page.summary() for page in self._scan_pages()]
        pages = self._filter_summaries(self._page_cache, kind=kind, visibility=visibility, q=q)
        total = len(pages)
        if offset:
            pages = pages[offset:]
        if limit is not None:
            pages = pages[:limit]
        return pages, total

    def list_pages_meta_with_count(
        self,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[PageSummary], int]:
        """Return metadata-only summaries and total count in one cache pass."""
        if self._meta_cache is None:
            self._meta_cache = self._scan_pages_meta()
        pages = self._filter_summaries(self._meta_cache, kind=kind, visibility=visibility, q=q)
        total = len(pages)
        if offset:
            pages = pages[offset:]
        if limit is not None:
            pages = pages[:limit]
        return pages, total

    def _filter_summaries(
        self,
        summaries: list[PageSummary],
        *,
        kind: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
    ) -> list[PageSummary]:
        pages = list(summaries)
        if kind:
            pages = [page for page in pages if page.kind == kind]
        if visibility:
            pages = [page for page in pages if page.visibility == visibility]
        if q:
            needle = q.casefold()
            pages = [page for page in pages if self._summary_matches_query(page, needle)]
        pages.sort(key=lambda page: (page.kind, page.id))
        return pages

    def _summary_matches_query(self, page: PageSummary, needle: str) -> bool:
        return (
            needle in page.id.casefold()
            or needle in page.title.casefold()
            or any(needle in tag.casefold() for tag in page.tags)
            or (page.summary is not None and needle in page.summary.casefold())
        )

    def read_page(self, page_id: str) -> PageDetail | None:
        normalized = normalize_page_id(page_id)
        path = self.page_path(normalized)
        if not path.is_file():
            return None
        return self._read_file(path, normalized).detail()

    def read_pages(self, ids: list[str]) -> dict[str, PageDetail]:
        """Read multiple pages keyed by page ID."""
        result: dict[str, PageDetail] = {}
        for page_id in ids:
            page = self.read_page(page_id)
            if page is not None:
                result[page_id] = page
        return result

    def iter_pages(
        self,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
    ) -> Iterator[PageDetail]:
        """Yield page details in one scan pass without re-reading each page."""
        needle = q.casefold() if q else None
        for page in self._scan_pages():
            summary = page.summary()
            if kind and summary.kind != kind:
                continue
            if visibility and summary.visibility != visibility:
                continue
            if needle and not self._summary_matches_query(summary, needle):
                continue
            yield page.detail()

    def upsert_page(self, page_id: str, content: str) -> PageDetail:
        normalized = normalize_page_id(page_id)
        path = self.page_path(normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized_content.endswith("\n"):
            normalized_content += "\n"
        path.write_text(normalized_content, encoding="utf-8")
        self._page_cache = None
        self._meta_cache = None
        return self._read_file(path, normalized).detail()

    def delete_page(self, page_id: str) -> bool:
        normalized = normalize_page_id(page_id)
        path = self.page_path(normalized)
        if not path.is_file():
            return False
        path.unlink()
        self._page_cache = None
        self._meta_cache = None
        return True

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        limit: int = 20,
        ledger: Any | None = None,
    ) -> SearchResponse:
        cleaned_query = " ".join(query.split())
        terms = [term.casefold() for term in re.findall(r"[A-Za-z0-9._-]+", cleaned_query)]
        if not terms:
            return SearchResponse(query=cleaned_query, hits=[])
        ledger_candidates_by_page = build_candidate_page_index(ledger)

        if self._search_index is not None and self._search_index_has_pages():
            hits: list[SearchHit] = []
            for hit in self._search_index.search(cleaned_query, kind=kind, limit=limit):
                page = self.read_page(str(hit["page_id"]))
                if page is None:
                    continue
                if visibility and page.visibility != visibility:
                    continue
                hits.append(
                    build_search_hit(
                        page,
                        score=int(hit["score"]),
                        matches=list(hit.get("matched_fields") or []),
                        ledger_candidates_by_page=ledger_candidates_by_page,
                    )
                )

            hits.sort(key=lambda hit: (-hit.score, hit.page.id))
            return SearchResponse(query=cleaned_query, hits=hits[:limit])

        hits: list[SearchHit] = []
        for page in self._scan_pages():
            summary = page.summary()
            if kind and summary.kind != kind:
                continue
            if visibility and summary.visibility != visibility:
                continue

            score = score_page(page, summary, terms)
            if score <= 0:
                continue

            hits.append(
                build_search_hit(
                    page.detail(),
                    score=score,
                    matches=extract_matches(page.body, terms),
                    ledger_candidates_by_page=ledger_candidates_by_page,
                )
            )

        hits.sort(key=lambda hit: (-hit.score, hit.page.id))
        return SearchResponse(query=cleaned_query, hits=hits[:limit])

    def catalog(self) -> CatalogResponse:
        pages = self.list_pages_meta()
        return CatalogResponse(
            kinds=sorted({page.kind for page in pages}),
            visibilities=sorted({page.visibility for page in pages}),
            tags=sorted({tag for page in pages for tag in page.tags}),
        )

    def page_path(self, page_id: str) -> Path:
        path = (self.root / f"{page_id}.md").resolve()
        if not path.is_relative_to(self.root):
            raise InvalidPageId("Page ID resolves outside the lore content directory.")
        return path

    def _scan_pages(self) -> list[MarkdownPage]:
        self.ensure_root()
        pages: list[MarkdownPage] = []
        for path in self.root.rglob("*.md"):
            if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            page_id = path.relative_to(self.root).with_suffix("").as_posix()
            try:
                normalize_page_id(page_id)
            except InvalidPageId:
                continue
            pages.append(self._read_file(path, page_id))
        return pages

    def _scan_pages_meta(self) -> list[PageSummary]:
        """Scan pages producing summaries without persistent MarkdownPage objects."""
        self.ensure_root()
        summaries: list[PageSummary] = []
        for path in self.root.rglob("*.md"):
            if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            page_id = path.relative_to(self.root).with_suffix("").as_posix()
            try:
                normalize_page_id(page_id)
            except InvalidPageId:
                continue
            summaries.append(self._read_file_summary(path, page_id))
        return summaries

    def _read_file_summary(self, path: Path, page_id: str) -> PageSummary:
        content = path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        normalized_frontmatter = normalize_frontmatter(frontmatter)
        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        return PageSummary(
            id=page_id,
            title=page_title(page_id, body, normalized_frontmatter),
            kind=str(normalized_frontmatter.get("kind") or infer_kind(page_id)),
            visibility=str(normalized_frontmatter.get("visibility") or "internal"),
            status=optional_string(normalized_frontmatter.get("status")),
            summary=optional_string(normalized_frontmatter.get("summary")),
            tags=string_list(normalized_frontmatter.get("tags")),
            sources=string_list(normalized_frontmatter.get("sources")),
            source_task=optional_string(normalized_frontmatter.get("source_task")),
            decision_id=optional_string(normalized_frontmatter.get("decision_id")),
            trace_id=optional_string(normalized_frontmatter.get("trace_id")),
            tool_calls=dict_list(normalized_frontmatter.get("tool_calls")),
            constraints=string_list(normalized_frontmatter.get("constraints")),
            policies_applied=string_list(normalized_frontmatter.get("policies_applied")),
            epistemic_status=epistemic_status_value(normalized_frontmatter.get("epistemic_status")),
            updated_at=updated_at,
            size=stat.st_size,
        )

    def _read_file(self, path: Path, page_id: str) -> MarkdownPage:
        content = path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        normalized_frontmatter = normalize_frontmatter(frontmatter)
        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        return MarkdownPage(
            id=page_id,
            path=path,
            content=content,
            body=body,
            frontmatter=normalized_frontmatter,
            updated_at=updated_at,
            size=stat.st_size,
        )

    def _get_summary_from_cache(self, page_id: str) -> PageSummary | None:
        for page in self.list_pages():
            if page.id == page_id:
                return page
        return None

    def _search_index_has_pages(self) -> bool:
        if self._search_index is None:
            return False
        row = self._search_index._conn.execute("SELECT 1 FROM pages LIMIT 1").fetchone()
        return row is not None


def build_candidate_page_index(ledger: Any | None, *, limit: int = 500) -> dict[str, list[dict[str, Any]]]:
    if ledger is None:
        return {}
    try:
        candidates = ledger.get_candidates(limit=limit)
    except Exception:
        return {}
    indexed: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        for page_id in string_list(candidate.get("source_page_ids")):
            indexed.setdefault(page_id, []).append(candidate)
    return indexed


def page_source_refs(frontmatter: dict[str, Any]) -> list[str]:
    provenance = frontmatter.get("provenance")
    provenance_source_paths = string_list(provenance.get("source_paths")) if isinstance(provenance, dict) else []
    refs = (
        string_list(frontmatter.get("source_paths"))
        + string_list(frontmatter.get("source_urls"))
        + provenance_source_paths
    )
    deduped: list[str] = []
    for ref in refs:
        if ref and ref not in deduped:
            deduped.append(ref)
    return deduped


def page_result_provenance(
    page: PageDetail,
    *,
    ledger_candidates_by_page: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    frontmatter = page.frontmatter
    candidates = (ledger_candidates_by_page or {}).get(page.id, [])
    candidate = candidates[0] if candidates else None
    return {
        "observed_at": optional_string(frontmatter.get("observed_at")),
        "valid_from": optional_string(candidate.get("valid_from")) if candidate else None,
        "valid_until": optional_string(candidate.get("valid_until")) if candidate else None,
        "actor": optional_string(frontmatter.get("actor")),
        "lane": optional_string(candidate.get("lane")) if candidate else optional_string(frontmatter.get("lane")),
        "source_refs": page_source_refs(frontmatter),
    }


def build_search_hit(
    page: PageDetail,
    *,
    score: int,
    matches: list[str],
    ledger_candidates_by_page: dict[str, list[dict[str, Any]]] | None = None,
) -> SearchHit:
    return SearchHit(
        page=PageSummary(**page.model_dump(include=set(PageSummary.model_fields))),
        score=score,
        matches=matches,
        **page_result_provenance(page, ledger_candidates_by_page=ledger_candidates_by_page),
    )


def normalize_page_id(raw_page_id: str) -> str:
    page_id = str(raw_page_id or "").strip().replace("\\", "/").strip("/")
    if page_id.endswith(".md"):
        page_id = page_id[:-3]
    if not page_id:
        raise InvalidPageId("Page ID is required.")

    parts = [part for part in page_id.split("/") if part]
    for part in parts:
        if part in {".", ".."} or not PAGE_SEGMENT_PATTERN.fullmatch(part):
            raise InvalidPageId(
                "Page IDs must use slash-separated path segments containing letters, numbers, '.', '_' or '-'."
            )
    return "/".join(parts)


def page_title(page_id: str, body: str, frontmatter: dict[str, Any]) -> str:
    explicit_title = optional_string(frontmatter.get("title"))
    if explicit_title:
        return explicit_title
    match = HEADING_PATTERN.search(body)
    if match:
        return match.group(1).strip()
    return page_id.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()


def infer_kind(page_id: str) -> str:
    first_segment = page_id.split("/", 1)[0]
    return {
        "projects": "project",
        "services": "service",
        "decisions": "decision",
        "runbooks": "runbook",
        "changelog": "changelog",
    }.get(first_segment, first_segment.rstrip("s") or "page")


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


def dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def epistemic_status_value(value: Any) -> EpistemicStatus | None:
    if isinstance(value, EpistemicStatus):
        return value
    cleaned = optional_string(value)
    if not cleaned:
        return None
    try:
        return EpistemicStatus(cleaned)
    except ValueError:
        return None


def normalize_frontmatter(frontmatter: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(frontmatter)
    epistemic_status = epistemic_status_value(normalized.get("epistemic_status"))
    if epistemic_status is not None:
        normalized["epistemic_status"] = epistemic_status
    return normalized


def score_page(page: MarkdownPage, summary: PageSummary, terms: list[str]) -> int:
    title = summary.title.casefold()
    page_id = summary.id.casefold()
    tags = " ".join(summary.tags).casefold()
    summary_text = (summary.summary or "").casefold()
    body = page.body.casefold()

    score = 0
    for term in terms:
        if term in title:
            score += 12
        if term in page_id:
            score += 8
        if term in tags:
            score += 6
        if term in summary_text:
            score += 5
        if term in body:
            score += 1 + min(body.count(term), 5)
    return score


def extract_matches(body: str, terms: list[str]) -> list[str]:
    matches: list[str] = []
    for line in body.splitlines():
        cleaned = " ".join(line.split())
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if any(term in lowered for term in terms):
            matches.append(cleaned[:240])
        if len(matches) >= 3:
            break
    return matches
