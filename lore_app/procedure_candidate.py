"""Find repeated captures and promote them into procedure candidates."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .capture import slugify, unique_page_id
from .frontmatter import frontmatter_scalar
from .repository import InvalidPageId, LoreRepository, optional_string
from .schemas import (
    PageSummary,
    ProcedureCandidateResponse,
    RepeatedCaptureGroup,
)

STOP_WORDS = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "were",
        "will",
        "would",
        "could",
        "should",
        "about",
        "which",
        "their",
        "there",
        "these",
        "those",
        "when",
        "where",
        "what",
        "while",
        "after",
        "before",
        "between",
    }
)
JACCARD_THRESHOLD = 0.4


def extract_keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", text.lower())
    return set(w for w in words if w not in STOP_WORDS)


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class _UnionFind:
    """Disjoint-set structure for grouping captures by similarity."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def find_repeated_captures(repo: LoreRepository) -> list[RepeatedCaptureGroup]:
    """Scan captures and group those with similar content via keyword overlap."""
    captures = [page for page in repo.list_pages(kind="capture") if page.id.startswith(("inbox/", "notes/"))]
    if len(captures) < 2:
        return []

    # Pre-compute keyword sets from title + observation body.
    keyword_sets: list[set[str]] = []
    for cap in captures:
        detail = repo.read_page(cap.id)
        if detail is None:
            keyword_sets.append(set())
            continue
        text = f"{cap.title} {detail.body}"
        keyword_sets.append(extract_keywords(text))

    n = len(captures)
    uf = _UnionFind(n)

    for i in range(n):
        if not keyword_sets[i]:
            continue
        for j in range(i + 1, n):
            if not keyword_sets[j]:
                continue
            if jaccard_similarity(keyword_sets[i], keyword_sets[j]) >= JACCARD_THRESHOLD:
                uf.union(i, j)

    # Collect groups keyed by root index.
    group_map: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        group_map.setdefault(root, []).append(i)

    groups: list[RepeatedCaptureGroup] = []
    for indices in group_map.values():
        if len(indices) < 2:
            continue
        group_captures = [captures[i] for i in indices]

        # Find common keywords across all captures in the group.
        common = keyword_sets[indices[0]]
        for i in indices[1:]:
            common = common & keyword_sets[i]
        group_key = "-".join(sorted(common)[:5]) if common else "similar-captures"

        # Auto-generate a title and trigger from top common keywords.
        sorted_common = sorted(common) if common else sorted(kw for i in indices for kw in keyword_sets[i])
        title_words = sorted_common[:5] if sorted_common else ["repeated", "pattern"]
        suggested_title = " ".join(w.title() for w in title_words) + " Procedure"
        suggested_trigger = (
            f"When {title_words[0]} occurs repeatedly" if title_words else "When repeated pattern detected"
        )

        groups.append(
            RepeatedCaptureGroup(
                group_key=group_key,
                captures=group_captures,
                count=len(group_captures),
                suggested_title=suggested_title,
                suggested_trigger=suggested_trigger,
            )
        )

    groups.sort(key=lambda g: (-g.count, g.group_key))
    return groups


def propose_procedure_candidate(
    repo: LoreRepository,
    capture_ids: list[str],
    *,
    title: str | None = None,
    trigger: str | None = None,
    lane: str | None = None,
) -> ProcedureCandidateResponse:
    """Create a procedure candidate page from repeated capture IDs."""
    if len(capture_ids) < 2:
        raise ValueError("At least 2 capture IDs are required.")

    source_captures: list[PageSummary] = []
    for raw_id in capture_ids:
        page_id = raw_id.strip()
        if not page_id:
            continue
        detail = repo.read_page(page_id)
        if detail is None:
            raise InvalidPageId(f"Capture page not found: {page_id}")
        if detail.frontmatter.get("kind") != "capture" or not detail.id.startswith(("inbox/", "notes/")):
            raise InvalidPageId(f"Not a capture page: {page_id}")
        source_captures.append(detail)

    if len(source_captures) < 2:
        raise ValueError("At least 2 valid capture IDs are required.")

    # Derive title and trigger if not provided.
    resolved_title = optional_string(title) or _derive_title(source_captures)
    resolved_trigger = optional_string(trigger) or f"When {resolved_title.split()[0].lower()} occurs repeatedly"

    page_id = unique_page_id(repo, f"procedures/candidates/{slugify(resolved_title)}")
    content = _build_candidate_markdown(
        title=resolved_title,
        trigger=resolved_trigger,
        source_capture_ids=[c.id for c in source_captures],
        lane=optional_string(lane),
    )
    page = repo.upsert_page(page_id, content)

    return ProcedureCandidateResponse(
        page=page,
        source_captures=source_captures,
        message=f"Created procedure candidate {page.id} from {len(source_captures)} captures.",
    )


def _derive_title(captures: list[PageSummary]) -> str:
    """Generate a title from the most common words across capture titles."""
    all_words: list[str] = []
    for cap in captures:
        all_words.extend(re.findall(r"[a-z]{4,}", cap.title.lower()))
    if not all_words:
        return "Repeated Capture Procedure"
    word_freq: dict[str, int] = {}
    for w in all_words:
        word_freq[w] = word_freq.get(w, 0) + 1
    top = sorted(word_freq, key=lambda w: (-word_freq[w], w))[:5]
    return " ".join(w.title() for w in top) + " Procedure"


def _build_candidate_markdown(
    *,
    title: str,
    trigger: str,
    source_capture_ids: list[str],
    lane: str | None,
) -> str:
    now = datetime.now(UTC).isoformat()
    frontmatter_lines = [
        "---",
        f"title: {frontmatter_scalar(title)}",
        "kind: procedure-candidate",
        "visibility: internal",
        "status: draft",
        f"trigger: {frontmatter_scalar(trigger)}",
        "steps: []",
        'schema_version: "1.0"',
        'author: ""',
        "validated: false",
        "validated_at: null",
        "proposed_from: repeated-pattern",
        f"proposed_at: {now}",
        "source_capture_ids:",
    ]
    for cid in source_capture_ids:
        frontmatter_lines.append(f"  - {cid}")
    if lane:
        frontmatter_lines.append(f"lane: {frontmatter_scalar(lane)}")
    frontmatter_lines.append("---")

    body_lines = [
        f"# {title}",
        "",
        f"> Auto-proposed procedure candidate from {len(source_capture_ids)} repeated captures.",
        "",
        "## Trigger",
        "",
        trigger,
        "",
        "## Steps",
        "",
        "_To be filled in after review._",
        "",
        "## Source Captures",
        "",
    ]
    for cid in source_capture_ids:
        body_lines.append(f"- [[{cid}]]")

    body_lines.append("")
    return "\n".join(frontmatter_lines + body_lines)
