from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .schemas import CatalogResponse, PageDetail, PageSummary, SearchHit, SearchResponse

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
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self._page_cache: list[PageSummary] | None = None

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def list_pages(
        self,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
        limit: int | None = None,
    ) -> list[PageSummary]:
        if self._page_cache is None:
            self._page_cache = [page.summary() for page in self._scan_pages()]
        pages = list(self._page_cache)
        if kind:
            pages = [page for page in pages if page.kind == kind]
        if visibility:
            pages = [page for page in pages if page.visibility == visibility]
        if q:
            needle = q.casefold()
            pages = [
                page
                for page in pages
                if needle in page.id.casefold()
                or needle in page.title.casefold()
                or any(needle in tag.casefold() for tag in page.tags)
                or (page.summary and needle in page.summary.casefold())
            ]
        pages.sort(key=lambda page: (page.kind, page.id))
        return pages[:limit] if limit else pages

    def read_page(self, page_id: str) -> PageDetail | None:
        normalized = normalize_page_id(page_id)
        path = self.page_path(normalized)
        if not path.is_file():
            return None
        return self._read_file(path, normalized).detail()

    def upsert_page(self, page_id: str, content: str) -> PageDetail:
        normalized = normalize_page_id(page_id)
        path = self.page_path(normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized_content.endswith("\n"):
            normalized_content += "\n"
        path.write_text(normalized_content, encoding="utf-8")
        self._page_cache = None
        return self._read_file(path, normalized).detail()

    def delete_page(self, page_id: str) -> bool:
        normalized = normalize_page_id(page_id)
        path = self.page_path(normalized)
        if not path.is_file():
            return False
        path.unlink()
        self._page_cache = None
        return True

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        visibility: str | None = None,
        limit: int = 20,
    ) -> SearchResponse:
        cleaned_query = " ".join(query.split())
        terms = [term.casefold() for term in re.findall(r"[A-Za-z0-9._-]+", cleaned_query)]
        if not terms:
            return SearchResponse(query=cleaned_query, hits=[])

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

            hits.append(SearchHit(page=summary, score=score, matches=extract_matches(page.body, terms)))

        hits.sort(key=lambda hit: (-hit.score, hit.page.id))
        return SearchResponse(query=cleaned_query, hits=hits[:limit])

    def catalog(self) -> CatalogResponse:
        pages = [page.summary() for page in self._scan_pages()]
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

    def _read_file(self, path: Path, page_id: str) -> MarkdownPage:
        content = path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        return MarkdownPage(
            id=page_id,
            path=path,
            content=content,
            body=body,
            frontmatter=frontmatter,
            updated_at=updated_at,
            size=stat.st_size,
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
