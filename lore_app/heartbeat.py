"""Heartbeat review: aggregates stale pages, contradictions, low-confidence, expired facts, and procedure issues."""
from __future__ import annotations

from datetime import datetime, timezone

from .link_graph import LinkGraphCache, LinkGraphResponse, build_link_graph
from .lint import lint_lore
from .lint_config import LintConfig
from .repository import LoreRepository
from .schemas import HeartbeatCategory, HeartbeatResponse

PROCEDURE_RULES = {"procedure_missing_steps", "procedure_missing_trigger", "procedure_step_not_in_body"}


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
        if issue.rule in ("missing_title", "missing_kind", "missing_visibility", "missing_summary", "missing_frontmatter") and not issue.suppressed
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
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_issues=total,
        stale_pages=HeartbeatCategory(count=len(stale_items), items=stale_items),
        missing_metadata=HeartbeatCategory(count=len(missing_items), items=missing_items),
        contradictions=HeartbeatCategory(count=len(contradiction_items), items=contradiction_items),
        low_confidence=HeartbeatCategory(count=len(low_confidence_items), items=low_confidence_items),
        expired_facts=HeartbeatCategory(count=len(expired_fact_items), items=expired_fact_items),
        procedure_issues=HeartbeatCategory(count=len(procedure_issue_items), items=procedure_issue_items),
    )
