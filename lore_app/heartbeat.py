"""Heartbeat review and self-audit capture generation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from .capture import capture_memory
from .link_graph import LinkGraphResponse, build_link_graph
from .lint import lint_lore
from .lint_config import LintConfig
from .schemas import CaptureRequest, HeartbeatCategory, HeartbeatResponse, PageDetail

if TYPE_CHECKING:
    from .repository import LoreRepository

PROCEDURE_RULES = {"procedure_missing_steps", "procedure_missing_trigger", "procedure_step_not_in_body"}
HEARTBEAT_CAPTURE_SOURCE_TASK = "heartbeat-self-audit"
HEARTBEAT_CAPTURE_PREFIX = "Heartbeat audit:"


def _category_label(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _page_frontmatter_value(repo: LoreRepository, page_id: str, key: str) -> str:
    page = repo.read_page(page_id)
    if page is None:
        return ""
    value = page.frontmatter.get(key)
    return str(value) if value is not None else ""


def _days_stale(stale_after: str) -> int:
    try:
        parsed = date.fromisoformat(stale_after[:10])
    except ValueError:
        return 0
    return max(0, (datetime.now(UTC).date() - parsed).days)


def _format_stale_pages(repo: LoreRepository, items: list[dict[str, Any]]) -> str:
    lines = ["Heartbeat found stale pages that need review:"]
    for item in items:
        page_id = str(item.get("page_id") or "")
        stale_after = str(item.get("stale_after") or "") or _page_frontmatter_value(repo, page_id, "stale_after")
        days_stale = item.get("days_stale")
        if not days_stale and stale_after:
            days_stale = _days_stale(stale_after)
        detail = f"{page_id} ({days_stale or 0} days stale"
        if stale_after:
            detail += f"; stale_after {stale_after}"
        detail += ")"
        lines.append(f"- {detail}")
    return "\n".join(lines)


def _format_contradictions(repo: LoreRepository, items: list[dict[str, Any]]) -> str:
    lines = ["Heartbeat found contradiction markers that need resolution:"]
    for item in items:
        page_id = str(item.get("page_id") or "")
        matched_text = str(item.get("matched_text") or "").strip()
        suffix = f": {matched_text}" if matched_text else ""
        lines.append(f"- {page_id}{suffix}")
    return "\n".join(lines)


def _format_low_confidence(repo: LoreRepository, items: list[dict[str, Any]]) -> str:
    lines = ["Heartbeat found low-confidence pages that need verification:"]
    for item in items:
        page_id = str(item.get("page_id") or "")
        confidence = str(item.get("confidence") or "").strip()
        suffix = f" ({confidence})" if confidence else ""
        lines.append(f"- {page_id}{suffix}")
    return "\n".join(lines)


def _format_expired_facts(repo: LoreRepository, items: list[dict[str, Any]]) -> str:
    lines = ["Heartbeat found expired facts that need review or archival:"]
    for item in items:
        page_id = str(item.get("page_id") or "")
        valid_until = str(item.get("valid_until") or "") or _page_frontmatter_value(repo, page_id, "valid_until")
        suffix = f" (valid_until {valid_until})" if valid_until else ""
        lines.append(f"- {page_id}{suffix}")
    return "\n".join(lines)


def _format_procedure_issues(repo: LoreRepository, items: list[dict[str, Any]]) -> str:
    lines = ["Heartbeat found procedure quality issues:"]
    for item in items:
        page_id = str(item.get("page_id") or "")
        rule = str(item.get("rule") or "")
        message = str(item.get("message") or "")
        lines.append(f"- {page_id}: {rule} - {message}")
    return "\n".join(lines)


HEARTBEAT_CAPTURE_CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "key": "stale_pages",
        "singular": "stale page",
        "plural": "stale pages",
        "formatter": _format_stale_pages,
    },
    {
        "key": "contradictions",
        "singular": "contradiction",
        "plural": "contradictions",
        "formatter": _format_contradictions,
    },
    {
        "key": "low_confidence",
        "singular": "low-confidence page",
        "plural": "low-confidence pages",
        "formatter": _format_low_confidence,
    },
    {
        "key": "expired_facts",
        "singular": "expired fact",
        "plural": "expired facts",
        "formatter": _format_expired_facts,
    },
    {
        "key": "procedure_issues",
        "singular": "procedure issue",
        "plural": "procedure issues",
        "formatter": _format_procedure_issues,
    },
)


def heartbeat_review(
    repo: LoreRepository,
    config: LintConfig | None = None,
    graph: LinkGraphResponse | None = None,
) -> HeartbeatResponse:
    """Run all freshness and quality checks and return an aggregated heartbeat report."""
    config = config or LintConfig()
    graph = graph or build_link_graph(repo)

    # Full lint for all checks in one pass
    lint_result = lint_lore(repo, config=config, graph=graph)

    stale_items = [
        {
            "page_id": issue.page_id,
            "title": issue.title,
            "stale_after": issue.detail or "",
            "days_stale": 0,
        }
        for issue in lint_result.issues
        if issue.rule == "stale_page" and not issue.suppressed
    ]

    missing_items = [
        {"page_id": issue.page_id, "title": issue.title, "kind": ""}
        for issue in lint_result.issues
        if issue.rule
        in ("missing_title", "missing_kind", "missing_visibility", "missing_summary", "missing_frontmatter")
        and not issue.suppressed
    ]

    contradiction_items = [
        {
            "page_id": issue.page_id,
            "title": issue.title,
            "line_number": 0,
            "matched_text": issue.detail or "",
        }
        for issue in lint_result.issues
        if issue.rule == "contradiction_marker" and not issue.suppressed
    ]

    low_confidence_items = [
        {"page_id": issue.page_id, "title": issue.title, "confidence": issue.message}
        for issue in lint_result.issues
        if issue.rule == "low_confidence" and not issue.suppressed
    ]

    expired_fact_items = [
        {"page_id": issue.page_id, "title": issue.title, "valid_until": issue.detail or ""}
        for issue in lint_result.issues
        if issue.rule == "expired_fact" and not issue.suppressed
    ]

    procedure_issue_items = [
        {"page_id": issue.page_id, "title": issue.title, "rule": issue.rule, "message": issue.message}
        for issue in lint_result.issues
        if issue.rule in PROCEDURE_RULES and not issue.suppressed
    ]

    total = (
        len(stale_items)
        + len(missing_items)
        + len(contradiction_items)
        + len(low_confidence_items)
        + len(expired_fact_items)
        + len(procedure_issue_items)
    )

    return HeartbeatResponse(
        generated_at=datetime.now(UTC).isoformat(),
        total_issues=total,
        stale_pages=HeartbeatCategory(count=len(stale_items), items=stale_items),
        missing_metadata=HeartbeatCategory(count=len(missing_items), items=missing_items),
        contradictions=HeartbeatCategory(count=len(contradiction_items), items=contradiction_items),
        low_confidence=HeartbeatCategory(count=len(low_confidence_items), items=low_confidence_items),
        expired_facts=HeartbeatCategory(count=len(expired_fact_items), items=expired_fact_items),
        procedure_issues=HeartbeatCategory(count=len(procedure_issue_items), items=procedure_issue_items),
    )


def emit_heartbeat_captures(
    repo: LoreRepository,
    config: LintConfig | None = None,
    graph: LinkGraphResponse | None = None,
) -> list[PageDetail]:
    """Create one inbox capture draft per heartbeat issue category, once per category per day."""
    review = heartbeat_review(repo, config=config, graph=graph)
    existing_categories = existing_heartbeat_capture_categories(repo)
    captures: list[PageDetail] = []

    for category in HEARTBEAT_CAPTURE_CATEGORIES:
        key = category["key"]
        heartbeat_category = getattr(review, key)
        items = heartbeat_capture_items(repo, heartbeat_category.items)
        if not items or key in existing_categories:
            continue

        title = heartbeat_capture_title(
            len(items),
            singular=category["singular"],
            plural=category["plural"],
        )
        formatter = category["formatter"]
        observation = formatter(repo, items)
        related_pages = [str(item.get("page_id")) for item in items if item.get("page_id")]

        captures.append(
            capture_memory(
                repo,
                CaptureRequest(
                    title=title,
                    observation=observation,
                    agent="heartbeat",
                    actor="heartbeat",
                    namespace="inbox",
                    lane="ops",
                    confidence="low",
                    epistemic_status="hearsay",
                    source_task=HEARTBEAT_CAPTURE_SOURCE_TASK,
                    related_pages=related_pages,
                ),
            )
        )

    return captures


def heartbeat_capture_items(repo: LoreRepository, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        page_id = str(item.get("page_id") or "")
        page = repo.read_page(page_id) if page_id else None
        if page is not None and page.frontmatter.get("source_task") == HEARTBEAT_CAPTURE_SOURCE_TASK:
            continue
        filtered.append(item)
    return filtered


def heartbeat_capture_title(count: int, *, singular: str, plural: str) -> str:
    return f"{HEARTBEAT_CAPTURE_PREFIX} {count} {_category_label(count, singular, plural)}"


def heartbeat_capture_category_keys() -> list[str]:
    return [str(category["key"]) for category in HEARTBEAT_CAPTURE_CATEGORIES]


def existing_heartbeat_capture_categories(repo: LoreRepository) -> set[str]:
    today_prefix = f"inbox/{datetime.now(UTC).date().isoformat()}/"
    existing: set[str] = set()
    for page in repo.list_pages(kind="capture"):
        if not page.id.startswith(today_prefix) or not page.title.startswith(HEARTBEAT_CAPTURE_PREFIX):
            continue
        for category in HEARTBEAT_CAPTURE_CATEGORIES:
            labels = (category["singular"], category["plural"])
            if any(page.title.endswith(f" {label}") for label in labels):
                existing.add(str(category["key"]))
    return existing


def heartbeat_capture_category_for_title(title: str) -> str | None:
    if not title.startswith(HEARTBEAT_CAPTURE_PREFIX):
        return None
    for category in HEARTBEAT_CAPTURE_CATEGORIES:
        labels = (category["singular"], category["plural"])
        if any(title.endswith(f" {label}") for label in labels):
            return str(category["key"])
    return None
