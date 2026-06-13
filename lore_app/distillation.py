from __future__ import annotations

from datetime import UTC, date, datetime

from .frontmatter import frontmatter_scalar
from .repository import InvalidPageId, LoreRepository, optional_string
from .schemas import (
    DailyDistillCapture,
    DailyDistillRequest,
    DailyDistillResponse,
    PageDetail,
    PageSummary,
    PendingDay,
    PendingDaysResponse,
)


def _date_from_frontmatter(page: PageDetail) -> date | None:
    # Capture page IDs embed the intended session date; prefer that over write time.
    for part in page.id.split("/"):
        try:
            return date.fromisoformat(part)
        except ValueError:
            continue

    captured_at = optional_string(page.frontmatter.get("captured_at"))
    if not captured_at:
        return None
    try:
        return date.fromisoformat(captured_at[:10])
    except ValueError:
        return None


def get_daily_captures(repo: LoreRepository, target_date: date) -> list[PageSummary]:
    all_captures = [page for page in repo.list_pages(kind="capture") if page.id.startswith(("inbox/", "notes/"))]
    matching: list[PageSummary] = []
    for summary in all_captures:
        detail = repo.read_page(summary.id)
        if detail is None:
            continue
        capture_date = _date_from_frontmatter(detail)
        if capture_date == target_date:
            matching.append(summary)
    return matching


def get_pending_days(repo: LoreRepository) -> PendingDaysResponse:
    all_captures = [page for page in repo.list_pages(kind="capture") if page.id.startswith(("inbox/", "notes/"))]

    date_captures: dict[str, list[PageSummary]] = {}
    for summary in all_captures:
        detail = repo.read_page(summary.id)
        if detail is None:
            continue
        capture_date = _date_from_frontmatter(detail)
        if capture_date is None:
            continue
        date_key = capture_date.isoformat()
        date_captures.setdefault(date_key, []).append(summary)

    pending: list[PendingDay] = []
    for date_key in sorted(date_captures):
        daily_page_id = f"dailies/{date_key}"
        existing = repo.read_page(daily_page_id)
        if existing is None:
            pending.append(PendingDay(date=date_key, capture_count=len(date_captures[date_key])))

    return PendingDaysResponse(pending_days=pending, total=len(pending))


def distill_session_to_daily(
    repo: LoreRepository,
    captures: list[PageSummary],
    target_date: date,
    *,
    actor: str | None = None,
    status: str = "active",
    reviewed_at: str | None = None,
) -> dict:
    capture_details: list[PageDetail] = []
    for summary in captures:
        detail = repo.read_page(summary.id)
        if detail is not None:
            capture_details.append(detail)

    if not capture_details:
        return {
            "date": target_date.isoformat(),
            "page_id": "",
            "capture_count": 0,
            "captures": [],
            "content": "",
        }

    daily_page_id = f"dailies/{target_date.isoformat()}"
    lines: list[str] = [
        "---",
        f"title: {frontmatter_scalar(f'Daily Note {target_date.isoformat()}')}",
        "kind: daily-note",
        "visibility: internal",
        f"status: {status}",
        f"summary: Distilled daily note from {len(capture_details)} session capture(s).",
        "tags: [daily-note, distilled]",
        f"distilled_at: {datetime.now(UTC).isoformat()}",
    ]
    if reviewed_at:
        lines.append(f"reviewed_at: {reviewed_at}")
    if actor:
        lines.append(f"actor: {frontmatter_scalar(actor)}")
    lines.extend(
        [
            "sources:",
        ]
    )
    for detail in capture_details:
        lines.append(f"  - {detail.id}")
    lines.extend(
        [
            "---",
            "",
            f"# Daily Note — {target_date.isoformat()}",
            "",
            f"> Distilled from {len(capture_details)} session capture(s).",
            "",
        ]
    )

    for detail in capture_details:
        title = detail.title
        confidence = optional_string(detail.frontmatter.get("confidence"))
        lane = optional_string(detail.frontmatter.get("lane"))
        lines.append(f"## {title}")
        lines.append("")
        if confidence or lane:
            meta_parts: list[str] = []
            if confidence:
                meta_parts.append(f"confidence: {confidence}")
            if lane:
                meta_parts.append(f"lane: {lane}")
            lines.append(f"> {' | '.join(meta_parts)}")
            lines.append("")
        lines.append(detail.body.strip())
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"

    distill_captures = [
        DailyDistillCapture(
            page_id=d.id,
            title=d.title,
            summary=d.summary,
            confidence=optional_string(d.frontmatter.get("confidence")),
            lane=optional_string(d.frontmatter.get("lane")),
        )
        for d in capture_details
    ]

    return {
        "date": target_date.isoformat(),
        "page_id": daily_page_id,
        "capture_count": len(capture_details),
        "captures": distill_captures,
        "content": content,
    }


def distill_daily(
    repo: LoreRepository,
    payload: DailyDistillRequest,
) -> DailyDistillResponse:
    if payload.date:
        try:
            target_date = date.fromisoformat(payload.date[:10])
        except ValueError as exc:
            raise InvalidPageId("date must be an ISO date (YYYY-MM-DD).") from exc
    else:
        target_date = datetime.now(UTC).date()

    # Preserve promotion state: re-distilling a day whose note was already
    # promoted must not silently revert status -> active and drop reviewed_at.
    existing = repo.read_page(f"dailies/{target_date.isoformat()}")
    status = "active"
    reviewed_at: str | None = None
    if existing is not None and optional_string(existing.frontmatter.get("status")) == "promoted":
        status = "promoted"
        reviewed_at = optional_string(existing.frontmatter.get("reviewed_at"))

    captures = get_daily_captures(repo, target_date)
    result = distill_session_to_daily(
        repo,
        captures,
        target_date,
        actor=optional_string(payload.actor),
        status=status,
        reviewed_at=reviewed_at,
    )

    if result["capture_count"] == 0:
        return DailyDistillResponse(**result)

    page = repo.upsert_page(result["page_id"], result["content"])
    return DailyDistillResponse(
        date=result["date"],
        page_id=page.id,
        capture_count=result["capture_count"],
        captures=result["captures"],
        content=page.content,
    )


def promote_daily_note(repo: LoreRepository, target_date: date) -> str:
    daily_page_id = f"dailies/{target_date.isoformat()}"
    page = repo.read_page(daily_page_id)
    if page is None:
        raise InvalidPageId(f"No daily note found for {target_date.isoformat()}. Run distill first.")
    # Do NOT pre-seed an opening fence: the loop emits the page's own opening
    # '---' below. Seeding one produced a doubled '---\n---' that parsed as an
    # empty frontmatter block, demoting all real keys (kind/status/sources) into
    # the body and losing the 'status: promoted' contract.
    updated_lines: list[str] = []
    in_fm = False
    status_updated = False
    reviewed_added = False
    reviewed_at = datetime.now(UTC).isoformat()
    for line in page.content.splitlines():
        if line.strip() == "---":
            if not in_fm:
                in_fm = True
                updated_lines.append(line)
            else:
                if not status_updated:
                    updated_lines.append("status: promoted")
                    status_updated = True
                if not reviewed_added:
                    updated_lines.append(f"reviewed_at: {reviewed_at}")
                    reviewed_added = True
                in_fm = False
                updated_lines.append(line)
            continue
        if in_fm and line.startswith("status:"):
            updated_lines.append("status: promoted")
            status_updated = True
            continue
        if in_fm and line.startswith("reviewed_at:"):
            updated_lines.append(f"reviewed_at: {reviewed_at}")
            reviewed_added = True
            continue
        updated_lines.append(line)
    if status_updated:
        updated_content = "\n".join(updated_lines) + "\n"
        repo.upsert_page(daily_page_id, updated_content)
    return daily_page_id
