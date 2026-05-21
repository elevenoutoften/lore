from __future__ import annotations

import difflib
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from .link_graph import build_link_graph
from .lint_config import LintConfig
from .repository import LoreRepository, optional_string
from .schemas import ContradictionMatch, ContradictionReviewResponse, LintIssue, LinkGraphResponse, LoreLintResponse, PageDetail, PageSummary, StalePageEntry, StalePagesResponse

def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


RECOMMENDED_FRONTMATTER_FIELDS = ("title", "kind", "visibility", "summary")
SOURCE_OPTIONAL_PREFIXES = ("inbox/", "notes/")
CONTRADICTION_PATTERN = re.compile(r"\b(CONTRADICTION|CONFLICT|DISPUTED)\s*:|TODO\s*:\s*verify", re.IGNORECASE)
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def lint_lore(
    repo: LoreRepository,
    config: LintConfig | None = None,
    graph: LinkGraphResponse | None = None,
) -> LoreLintResponse:
    config = config or LintConfig()
    pages = repo.list_pages()
    page_details = read_page_details(repo, pages)
    issues: list[LintIssue] = []

    graph = graph or build_link_graph(repo)
    for edge in graph.broken_links:
        issues.append(
            LintIssue(
                rule="broken_internal_link",
                severity="error",
                page_id=edge.source,
                title=edge.source_title,
                target=edge.target or edge.href,
                message=f"Broken internal link to {edge.target or edge.href}.",
                detail=edge.label,
            )
        )

    for page in page_details.values():
        issues.extend(lint_frontmatter(page, config))
        issues.extend(lint_freshness(page))
        issues.extend(lint_confidence(page))
        issues.extend(lint_contradictions(page))
        issues.extend(lint_procedure(page))

    issues.extend(lint_duplicate_titles(pages))
    issues.extend(lint_orphans(pages, graph.links))
    issues = apply_config(issues, config)
    issues = annotate_suggestions(issues, page_details, graph)
    issues.sort(key=issue_sort_key)
    suppressed_count = sum(1 for issue in issues if issue.suppressed)

    return LoreLintResponse(
        checked_pages=len(pages),
        issue_count=len(issues),
        suppressed_count=suppressed_count,
        issues=issues,
    )


def suggest_fix(issue: LintIssue, page: PageDetail | None = None) -> tuple[str | None, bool]:
    """Return (suggestion_text, auto_fixable) for a lint issue."""
    suggestions = {
        "missing_frontmatter": ("Add YAML frontmatter with title, kind, visibility, and summary.", True),
        "missing_title": ('Add `title: "Page Title"` to frontmatter.', True),
        "missing_kind": ("Add `kind: service` (or project, concept, runbook) to frontmatter.", True),
        "missing_visibility": ("Add `visibility: internal` (or public) to frontmatter.", True),
        "missing_summary": ("Add a one-line `summary` field describing this page's purpose.", True),
        "missing_sources": ("Add `sources:` list referencing where this knowledge comes from.", True),
        "broken_internal_link": ("Update or remove the broken link. Check that the target page exists.", False),
        "orphan_page": ("Add a link to this page from another Lore page.", False),
        "duplicate_title": ("Rename one of the pages to have a unique title.", False),
        "stale_page": ("Review and update the page, then set a new `stale_after` date.", True),
        "low_confidence": ("Verify the information and update `confidence` to medium or high.", False),
        "contradiction_marker": ("Resolve the contradiction marker and update the content.", False),
        "procedure_missing_steps": ("Add a non-empty `steps:` list to the procedure frontmatter.", True),
        "procedure_missing_trigger": ("Add a `trigger:` field describing when to use this procedure.", True),
        "procedure_step_not_in_body": ("Add the missing numbered step to the procedure body.", False),
    }
    return suggestions.get(issue.rule, (None, False))


def annotate_suggestions(
    issues: list[LintIssue],
    page_details: dict[str, PageDetail],
    graph: LinkGraphResponse | None = None,
) -> list[LintIssue]:
    annotated: list[LintIssue] = []
    for issue in issues:
        page = page_details.get(issue.page_id or "")
        suggestion: str | None
        auto_fixable: bool
        if issue.rule == "orphan_page" and issue.page_id and graph is not None:
            suggestion = _orphan_suggestion(issue.page_id, page_details, graph)
            auto_fixable = False
            if suggestion is None:
                suggestion, auto_fixable = suggest_fix(issue, page)
        elif issue.rule == "broken_internal_link" and issue.target:
            suggestion = _broken_link_suggestion(issue.target, page_details.keys())
            auto_fixable = False
            if suggestion is None:
                suggestion, auto_fixable = suggest_fix(issue, page)
        else:
            suggestion, auto_fixable = suggest_fix(issue, page)
        data = issue.model_dump()
        data["suggestion"] = suggestion
        data["auto_fixable"] = auto_fixable
        annotated.append(LintIssue(**data))
    return annotated


def _orphan_suggestion(page_id: str, page_details: dict[str, PageDetail], graph: LinkGraphResponse) -> str | None:
    orphan = page_details.get(page_id)
    if orphan is None:
        return None

    inbound_targets = {
        edge.target
        for edge in graph.links
        if edge.exists and not edge.external and edge.target and edge.source != edge.target
    }
    orphan_tags = {tag.casefold() for tag in orphan.tags}
    orphan_title_words = title_keywords(orphan.title)
    candidates: list[tuple[int, str, str]] = []

    for candidate_id in sorted(inbound_targets):
        if candidate_id == page_id:
            continue
        candidate = page_details.get(candidate_id)
        if candidate is None:
            continue

        shared_tags = sorted(orphan_tags & {tag.casefold() for tag in candidate.tags})
        title_overlap = orphan_title_words & title_keywords(candidate.title)
        reasons: list[str] = []
        score = 0
        if shared_tags:
            reasons.append(f"shared tags: {', '.join(shared_tags)}")
            score += 10 + len(shared_tags)
        if len(title_overlap) >= 2:
            reasons.append("related title")
            score += len(title_overlap)
        if not reasons:
            continue
        candidates.append((score, candidate_id, f"`{candidate_id}` ({'; '.join(reasons)})"))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1]))
    formatted = ", ".join(item[2] for item in candidates[:3])
    return f"Consider linking from: {formatted}."


def _broken_link_suggestion(broken_target: str, page_ids: Any) -> str | None:
    matches = difflib.get_close_matches(broken_target, list(page_ids), n=2, cutoff=0.4)
    if not matches:
        return None
    if len(matches) == 1:
        return f"Did you mean `{matches[0]}`?"
    return "Did you mean " + " or ".join(f"`{match}`" for match in matches) + "?"


def title_keywords(title: str) -> set[str]:
    return {word.casefold() for word in re.findall(r"[A-Za-z0-9]+", title) if len(word) >= 4}


def read_page_details(repo: LoreRepository, pages: list[PageSummary]) -> dict[str, PageDetail]:
    details: dict[str, PageDetail] = {}
    for page in pages:
        detail = repo.read_page(page.id)
        if detail is not None:
            details[page.id] = detail
    return details


def lint_frontmatter(page: PageDetail, config: LintConfig | None = None) -> list[LintIssue]:
    config = config or LintConfig()
    frontmatter = page.frontmatter
    if not frontmatter:
        return [
            page_issue(
                page,
                rule="missing_frontmatter",
                severity="warning",
                message="Page has no frontmatter.",
            )
        ]

    issues: list[LintIssue] = []
    for field in RECOMMENDED_FRONTMATTER_FIELDS:
        if config.is_field_allowed_missing(field):
            continue
        if not optional_string(frontmatter.get(field)):
            issues.append(
                page_issue(
                    page,
                    rule=f"missing_{field}",
                    severity="warning",
                    message=f"Frontmatter is missing {field}.",
                )
            )

    if not page.sources and not page.id.startswith(SOURCE_OPTIONAL_PREFIXES):
        issues.append(
            page_issue(
                page,
                rule="missing_sources",
                severity="warning",
                message="Frontmatter is missing sources.",
            )
        )

    return issues


def apply_config(issues: list[LintIssue], config: LintConfig) -> list[LintIssue]:
    filtered: list[LintIssue] = []
    for issue in issues:
        if not config.is_rule_enabled(issue.rule):
            continue

        data = issue.model_dump()
        data["severity"] = config.get_severity(issue.rule, issue.severity)
        if issue.page_id and config.is_suppressed(issue.page_id, issue.rule):
            data["suppressed"] = True
            data["suppression_reason"] = config.suppression_reason(issue.page_id, issue.rule)
        filtered.append(LintIssue(**data))
    return filtered


def lint_freshness(page: PageDetail) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for field in ("reviewed_at", "stale_after"):
        raw_value = page.frontmatter.get(field)
        if raw_value is None:
            continue
        parsed = parse_frontmatter_date(raw_value)
        if parsed is None:
            issues.append(
                page_issue(
                    page,
                    rule=f"invalid_{field}",
                    severity="warning",
                    message=f"Frontmatter field {field} should be an ISO date.",
                    detail=str(raw_value),
                )
            )
            continue
        if field == "stale_after" and parsed < _utc_today():
            issues.append(
                page_issue(
                    page,
                    rule="stale_page",
                    severity="warning",
                    message=f"Page became stale on {parsed.isoformat()}.",
                )
            )
    # Bi-temporal: check for expired validity windows
    valid_until = page.frontmatter.get("valid_until")
    if valid_until:
        parsed = parse_frontmatter_date(valid_until)
        if parsed is None:
            issues.append(
                page_issue(
                    page,
                    rule="invalid_valid_until",
                    severity="warning",
                    message=f"Frontmatter field valid_until should be an ISO date.",
                    detail=str(valid_until),
                )
            )
        elif parsed < _utc_today():
            issues.append(
                page_issue(
                    page,
                    rule="expired_fact",
                    severity="warning",
                    message=f"Fact validity expired on {parsed.isoformat()}. Review or archive.",
                )
            )
    return issues


def lint_confidence(page: PageDetail) -> list[LintIssue]:
    confidence = optional_string(page.frontmatter.get("confidence"))
    if confidence is None or confidence.casefold() not in {"low", "unknown"}:
        return []
    return [
        page_issue(
            page,
            rule="low_confidence",
            severity="info",
            message=f"Page confidence is {confidence}.",
        )
    ]


def lint_contradictions(page: PageDetail) -> list[LintIssue]:
    for line in page.body.splitlines():
        cleaned = " ".join(line.split())
        if cleaned and CONTRADICTION_PATTERN.search(cleaned):
            return [
                page_issue(
                    page,
                    rule="contradiction_marker",
                    severity="warning",
                    message="Page contains a contradiction or verification marker.",
                    detail=cleaned[:240],
                )
            ]
    return []


def lint_procedure(page: PageDetail) -> list[LintIssue]:
    """Lint procedure-specific quality checks."""
    issues: list[LintIssue] = []
    if page.kind != "procedure":
        return issues

    steps = page.frontmatter.get("steps", [])
    if not steps or not isinstance(steps, list):
        issues.append(
            page_issue(
                page,
                rule="procedure_missing_steps",
                severity="error",
                message="Procedure page must have a 'steps' frontmatter list.",
            )
        )

    if not optional_string(page.frontmatter.get("trigger")):
        issues.append(
            page_issue(
                page,
                rule="procedure_missing_trigger",
                severity="warning",
                message="Procedure page should have a 'trigger' field describing when to use it.",
            )
        )

    if steps and isinstance(steps, list):
        body = page.body
        for index, step in enumerate(steps, 1):
            if str(index) not in body:
                issues.append(
                    page_issue(
                        page,
                        rule="procedure_step_not_in_body",
                        severity="info",
                        message=f"Step {index} not found in page body.",
                        detail=str(step),
                    )
                )

    return issues


def lint_stale_queue(repo: LoreRepository) -> StalePagesResponse:
    today = _utc_today()
    stale_pages: list[StalePageEntry] = []
    missing_metadata_pages: list[StalePageEntry] = []

    for summary in repo.list_pages():
        page = repo.read_page(summary.id)
        if page is None:
            continue
        stale_after = parse_frontmatter_date(page.frontmatter.get("stale_after"))
        reviewed_at = parse_frontmatter_date(page.frontmatter.get("reviewed_at"))

        if stale_after is not None and stale_after < today:
            stale_pages.append(
                StalePageEntry(
                    page_id=page.id,
                    title=page.title,
                    kind=page.kind,
                    stale_after=stale_after.isoformat(),
                    reviewed_at=reviewed_at.isoformat() if reviewed_at is not None else None,
                    days_stale=(today - stale_after).days,
                    severity="stale",
                )
            )
            continue

        if (
            page.frontmatter.get("stale_after") is None
            and page.frontmatter.get("reviewed_at") is None
            and not page.id.startswith(SOURCE_OPTIONAL_PREFIXES)
        ):
            missing_metadata_pages.append(
                StalePageEntry(
                    page_id=page.id,
                    title=page.title,
                    kind=page.kind,
                    severity="missing_metadata",
                )
            )

    stale_pages.sort(key=lambda entry: (-(entry.days_stale or 0), entry.page_id))
    missing_metadata_pages.sort(key=lambda entry: entry.page_id)
    return StalePagesResponse(
        total=len(stale_pages) + len(missing_metadata_pages),
        stale_pages=stale_pages,
        missing_metadata_pages=missing_metadata_pages,
    )


def lint_contradiction_review(repo: LoreRepository) -> ContradictionReviewResponse:
    contradictions: list[ContradictionMatch] = []
    pages_with_matches: set[str] = set()

    for summary in repo.list_pages():
        page = repo.read_page(summary.id)
        if page is None:
            continue
        lines = page.body.splitlines()
        for index, line in enumerate(lines):
            cleaned = " ".join(line.split())
            if not cleaned or not CONTRADICTION_PATTERN.search(cleaned):
                continue
            pages_with_matches.add(page.id)
            contradictions.append(
                ContradictionMatch(
                    page_id=page.id,
                    title=page.title,
                    line_number=index + 1,
                    matched_text=cleaned[:240],
                    context_before=lines[max(0, index - 2) : index],
                    context_after=lines[index + 1 : index + 3],
                )
            )

    contradictions.sort(key=lambda match: (match.page_id, match.line_number))
    return ContradictionReviewResponse(
        total_pages=len(pages_with_matches),
        total_markers=len(contradictions),
        contradictions=contradictions,
    )


def lint_duplicate_titles(pages: list[PageSummary]) -> list[LintIssue]:
    groups: dict[str, list[PageSummary]] = defaultdict(list)
    for page in pages:
        groups[normalize_title(page.title)].append(page)

    issues: list[LintIssue] = []
    for title_group in groups.values():
        if len(title_group) < 2:
            continue
        page_ids = sorted(page.id for page in title_group)
        for page in title_group:
            others = [page_id for page_id in page_ids if page_id != page.id]
            issues.append(
                LintIssue(
                    rule="duplicate_title",
                    severity="warning",
                    page_id=page.id,
                    title=page.title,
                    message=f"Title is also used by {', '.join(others)}.",
                )
            )
    return issues


def normalize_title(value: str) -> str:
    return " ".join(value.casefold().split())


def lint_orphans(pages: list[PageSummary], links: list[Any]) -> list[LintIssue]:
    inbound_targets = {
        link.target
        for link in links
        if getattr(link, "exists", False)
        and not getattr(link, "external", False)
        and getattr(link, "target", None)
        and getattr(link, "source", None) != getattr(link, "target", None)
    }
    issues: list[LintIssue] = []
    for page in pages:
        if page.id not in inbound_targets:
            issues.append(
                LintIssue(
                    rule="orphan_page",
                    severity="info",
                    page_id=page.id,
                    title=page.title,
                    message="No other Lore page links here.",
                )
            )
    return issues


def page_issue(
    page: PageDetail,
    *,
    rule: str,
    severity: str,
    message: str,
    target: str | None = None,
    detail: str | None = None,
) -> LintIssue:
    return LintIssue(
        rule=rule,
        severity=severity,
        page_id=page.id,
        title=page.title,
        message=message,
        target=target,
        detail=detail,
    )


def parse_frontmatter_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def issue_sort_key(issue: LintIssue) -> tuple[int, str, str, str, str]:
    return (
        SEVERITY_ORDER.get(issue.severity, 9),
        issue.page_id or "",
        issue.rule,
        issue.target or "",
        issue.message,
    )
