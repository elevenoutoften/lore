from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

from .frontmatter import frontmatter_scalar
from .provenance import merge_capture_provenance
from .repository import InvalidPageId, LoreRepository, infer_kind, normalize_page_id, optional_string, string_list
from .schemas import (
    CaptureDigestGroup,
    CaptureDigestResponse,
    CaptureListResponse,
    CaptureRequest,
    PageDetail,
    PagePromotionSource,
    PageSummary,
    PromotionAuditResponse,
    PromotionRecord,
    SourceCapture,
)


class CaptureNotFoundError(LookupError):
    """Raised when promoting a capture page id that does not exist."""


SLUG_PATTERN = re.compile(r"[A-Za-z0-9]+")
PAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_/.-]*$")
CAPTURE_STATUSES = {"draft", "review", "accepted", "rejected", "archived"}
ALLOWED_NAMESPACES = {"inbox", "notes"}
ALLOWED_LANES = {"project", "procedural", "ops", "companion", "draft"}
CAPTURE_INTAKE_SUMMARY = "Rough agent memory capture; not canonical truth."


def capture_memory(repo: LoreRepository, payload: CaptureRequest) -> PageDetail:
    if payload.namespace not in ALLOWED_NAMESPACES:
        raise InvalidPageId(
            f"Invalid namespace: {payload.namespace!r}. Must be one of: {', '.join(sorted(ALLOWED_NAMESPACES))}"
        )

    lane_value = optional_string(payload.lane)
    if lane_value and lane_value not in ALLOWED_LANES:
        raise InvalidPageId(f"Invalid lane: {lane_value!r}. Must be one of: {', '.join(sorted(ALLOWED_LANES))}")

    capture_date = parse_capture_date(payload.capture_date)
    title = optional_string(payload.title) or title_from_observation(payload.observation)
    slug = slugify(title)

    if payload.namespace == "notes":
        agent = slugify(payload.agent or payload.actor or "agent")
        base_page_id = f"notes/{agent}/{capture_date.isoformat()}/{slug}"
    else:
        base_page_id = f"inbox/{capture_date.isoformat()}/{slug}"

    for raw_id in payload.related_pages:
        if raw_id.strip():
            validate_page_id(raw_id, "related_pages")
    if payload.suggested_target_page and payload.suggested_target_page.strip():
        validate_page_id(payload.suggested_target_page, "suggested_target_page")
    for url in payload.source_urls:
        if not validate_source_url(url):
            raise InvalidPageId(f"Invalid source URL: {url!r}. Must start with http:// or https://")

    related_pages = normalize_page_ids(payload.related_pages)
    suggested_target_page = normalize_optional_page_id(payload.suggested_target_page)
    page_id = unique_page_id(repo, base_page_id)
    effective_source_task = optional_string(payload.source_task) or optional_string(payload.task_id)
    provenance = merge_capture_provenance(payload, related_pages=related_pages)
    content = build_capture_markdown(
        title=title,
        observation=payload.observation,
        captured_at=datetime.now(UTC).isoformat(),
        confidence=optional_string(payload.confidence) or "unknown",
        source_task=effective_source_task,
        related_pages=related_pages,
        suggested_target_page=suggested_target_page,
        sources=string_list(payload.sources),
        source_paths=string_list(payload.source_paths),
        source_urls=string_list(payload.source_urls),
        evidence=optional_string(payload.evidence),
        epistemic_status=payload.epistemic_status,
        actor=optional_string(payload.actor) or optional_string(payload.agent),
        lane=optional_string(payload.lane),
        observed_at=optional_string(payload.observed_at) or datetime.now(UTC).isoformat(),
        valid_from=optional_string(payload.valid_from),
        valid_until=optional_string(payload.valid_until),
        task_id=optional_string(payload.task_id),
        decision_id=optional_string(payload.decision_id),
        trace_id=optional_string(payload.trace_id),
        tool_calls=payload.tool_calls or None,
        constraints=string_list(payload.constraints) or None,
        policies_applied=string_list(payload.policies_applied) or None,
        tags=string_list(payload.tags) or None,
        provenance=provenance.model_dump(mode="json"),
    )
    return repo.upsert_page(page_id, content)


def list_captures(repo: LoreRepository, *, status: str | None = "draft", limit: int = 50) -> CaptureListResponse:
    captures = [page for page in repo.list_pages(kind="capture") if page.id.startswith(("inbox/", "notes/"))]
    status_filter = optional_string(status)
    if status_filter and status_filter.casefold() != "all":
        captures = [page for page in captures if page.status == status_filter]
    captures.sort(key=lambda page: page.id, reverse=True)
    limited = captures[: max(1, min(limit, 200))]
    return CaptureListResponse(status=status_filter, count=len(limited), captures=limited)


def build_capture_digest(repo: LoreRepository) -> CaptureDigestResponse:
    draft = list_captures(repo, status="draft", limit=200)
    review = list_captures(repo, status="review", limit=200)
    captures = draft.captures + review.captures

    by_date: dict[str, list[PageSummary]] = {}
    by_source_task: dict[str, list[PageSummary]] = {}
    by_suggested_target: dict[str, list[PageSummary]] = {}

    for capture in captures:
        detail = repo.read_page(capture.id)
        if detail is None:
            continue
        frontmatter = detail.frontmatter
        captured_at = optional_string(frontmatter.get("captured_at")) or ""
        date_key = captured_at[:10] if captured_at else "(no date)"
        source_task = optional_string(frontmatter.get("source_task")) or "(no task)"
        suggested_target = optional_string(frontmatter.get("suggested_target_page")) or "(no target)"

        by_date.setdefault(date_key, []).append(capture)
        by_source_task.setdefault(source_task, []).append(capture)
        by_suggested_target.setdefault(suggested_target, []).append(capture)

    return CaptureDigestResponse(
        total_draft=draft.count,
        total_review=review.count,
        by_date=groups_by_key(by_date, reverse_key=True),
        by_source_task=groups_by_count(by_source_task),
        by_suggested_target=groups_by_count(by_suggested_target),
    )


def groups_by_key(groups: dict[str, list[PageSummary]], *, reverse_key: bool = False) -> list[CaptureDigestGroup]:
    return [
        CaptureDigestGroup(key=key, count=len(captures), captures=captures)
        for key, captures in sorted(groups.items(), key=lambda item: item[0], reverse=reverse_key)
    ]


def groups_by_count(groups: dict[str, list[PageSummary]]) -> list[CaptureDigestGroup]:
    return [
        CaptureDigestGroup(key=key, count=len(captures), captures=captures)
        for key, captures in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    ]


def build_promotion_audit(repo: LoreRepository) -> PromotionAuditResponse:
    capture_summaries = [page for page in repo.list_pages(kind="capture") if is_capture_page_id(page.id)]
    capture_index = {page.id: page for page in capture_summaries}
    promoted_captures: list[PromotionRecord] = []

    for capture in capture_summaries:
        detail = repo.read_page(capture.id)
        if detail is None:
            continue
        target_page_id = optional_string(detail.frontmatter.get("promoted_to"))
        if target_page_id is None:
            continue
        promoted_captures.append(
            PromotionRecord(
                capture_id=capture.id,
                capture_title=capture.title,
                target_page_id=target_page_id,
                capture_status=capture.status,
            )
        )

    pages_with_sources: list[PagePromotionSource] = []
    for page in repo.list_pages():
        source_capture_ids = [source for source in string_list(page.sources) if is_capture_page_id(source)]
        if not source_capture_ids:
            continue
        pages_with_sources.append(
            PagePromotionSource(
                page_id=page.id,
                page_title=page.title,
                source_captures=[
                    SourceCapture(
                        capture_id=capture_id,
                        capture_title=capture_index[capture_id].title if capture_id in capture_index else None,
                        capture_status=capture_index[capture_id].status if capture_id in capture_index else None,
                    )
                    for capture_id in source_capture_ids
                ],
            )
        )

    return PromotionAuditResponse(
        promoted_captures=promoted_captures,
        pages_with_capture_sources=pages_with_sources,
    )


def transition_capture_status(repo: LoreRepository, page_id: str, new_status: str) -> PageDetail:
    page = repo.read_page(page_id)
    if page is None:
        raise InvalidPageId("Capture page not found.")
    if page.frontmatter.get("kind") != "capture" or not page.id.startswith(("inbox/", "notes/")):
        raise InvalidPageId("Only capture pages can be transitioned.")
    if new_status not in CAPTURE_STATUSES:
        valid = ", ".join(sorted(CAPTURE_STATUSES))
        raise ValueError(f"Invalid status. Must be one of: {valid}")

    updated_content = update_frontmatter_status(page.content, new_status)
    return repo.upsert_page(page.id, updated_content)


def promote_capture(
    repo: LoreRepository,
    page_id: str,
    *,
    target_page_id: str | None = None,
    content: str | None = None,
) -> PageDetail:
    capture = repo.read_page(page_id)
    if capture is None:
        raise CaptureNotFoundError(f"Capture not found: {page_id}")
    if capture.frontmatter.get("kind") != "capture" or not capture.id.startswith(("inbox/", "notes/")):
        raise InvalidPageId("Only capture pages can be promoted.")

    target = normalize_optional_page_id(target_page_id) or normalize_optional_page_id(
        optional_string(capture.frontmatter.get("suggested_target_page"))
    )
    if target is None:
        raise ValueError(
            "No target page specified. Provide target_page_id or set suggested_target_page in the capture."
        )

    explicit_content = optional_string(content)
    existing = repo.read_page(target)
    if existing is not None and explicit_content is None:
        raise ValueError(
            "Target page already exists. Provide explicit content to overwrite, or choose a different target."
        )

    target_content = (
        explicit_content if explicit_content is not None else build_promoted_capture_markdown(capture, target)
    )
    target_page = repo.upsert_page(target, target_content)

    updated_capture = update_frontmatter_status(capture.content, "accepted")
    updated_capture = update_frontmatter_promoted_to(updated_capture, target)
    repo.upsert_page(capture.id, updated_capture)
    return target_page


def update_frontmatter_status(content: str, new_status: str) -> str:
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    frontmatter = re.sub(
        r"^status:\s*.*$",
        f"status: {new_status}",
        parts[1],
        count=1,
        flags=re.MULTILINE,
    )
    return f"---{frontmatter}---{parts[2]}"


def update_frontmatter_promoted_to(content: str, target_page_id: str) -> str:
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    frontmatter = re.sub(
        r"^promoted_to:\s*.*\n?",
        "",
        parts[1],
        flags=re.MULTILINE,
    )
    promoted_line = f"promoted_to: {target_page_id}"
    if re.search(r"^status:\s*.*$", frontmatter, flags=re.MULTILINE):
        frontmatter = re.sub(
            r"^(status:\s*.*)$",
            rf"\1\n{promoted_line}",
            frontmatter,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        frontmatter = frontmatter.rstrip("\n") + f"\n{promoted_line}\n"
    return f"---{frontmatter}---{parts[2]}"


def build_promoted_capture_markdown(capture: PageDetail, target_page_id: str) -> str:
    tags = string_list(capture.frontmatter.get("tags"))
    frontmatter = [
        "---",
        f"title: {frontmatter_scalar(capture.title)}",
        f"kind: {infer_kind(target_page_id) or 'page'}",
        "visibility: internal",
        "status: active",
        f"summary: Promoted from capture {capture.id}",
        f"tags: {frontmatter_list(tags)}",
        "sources:",
        f"  - {capture.id}",
        "---",
        "",
    ]
    return "\n".join(frontmatter) + capture.body.rstrip() + "\n"


def build_capture_markdown(
    *,
    title: str,
    observation: str,
    captured_at: str,
    confidence: str,
    source_task: str | None,
    related_pages: list[str],
    suggested_target_page: str | None,
    sources: list[str],
    source_paths: list[str] | None = None,
    source_urls: list[str] | None = None,
    evidence: str | None = None,
    epistemic_status: str | None = None,
    actor: str | None = None,
    lane: str | None = None,
    observed_at: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    task_id: str | None = None,
    decision_id: str | None = None,
    trace_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    constraints: list[str] | None = None,
    policies_applied: list[str] | None = None,
    tags: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> str:
    effective_source_task = source_task or task_id
    merged_tags = ["capture", "agent-memory"]
    for tag in tags or []:
        cleaned = tag.strip()
        if cleaned and cleaned not in merged_tags:
            merged_tags.append(cleaned)
    frontmatter = [
        "---",
        f"title: {frontmatter_scalar(title)}",
        "kind: capture",
        "visibility: internal",
        "status: draft",
        f"summary: {CAPTURE_INTAKE_SUMMARY}",
        f"tags: {frontmatter_list(merged_tags)}",
        f"captured_at: {captured_at}",
        f"confidence: {frontmatter_scalar(confidence)}",
    ]
    if actor:
        frontmatter.append(f"actor: {frontmatter_scalar(actor)}")
    if lane:
        frontmatter.append(f"lane: {frontmatter_scalar(lane)}")
    if effective_source_task:
        frontmatter.append(f"source_task: {frontmatter_scalar(effective_source_task)}")
    if suggested_target_page:
        frontmatter.append(f"suggested_target_page: {suggested_target_page}")
    if related_pages:
        frontmatter.append("related:")
        frontmatter.extend(f"  - {page_id}" for page_id in related_pages)
    if sources:
        frontmatter.append("sources:")
        frontmatter.extend(f"  - {frontmatter_scalar(source)}" for source in sources)
    if source_paths:
        frontmatter.append("source_paths:")
        frontmatter.extend(f"  - {frontmatter_scalar(path)}" for path in source_paths)
    if source_urls:
        frontmatter.append("source_urls:")
        frontmatter.extend(f"  - {frontmatter_scalar(url)}" for url in source_urls)
    if evidence:
        frontmatter.append(f"evidence: {frontmatter_scalar(evidence)}")
    if epistemic_status:
        frontmatter.append(f"epistemic_status: {frontmatter_scalar(epistemic_status)}")
    if observed_at:
        frontmatter.append(f"observed_at: {frontmatter_scalar(observed_at)}")
    if valid_from:
        frontmatter.append(f"valid_from: {frontmatter_scalar(valid_from)}")
    if valid_until:
        frontmatter.append(f"valid_until: {frontmatter_scalar(valid_until)}")
    if decision_id:
        frontmatter.append(f"decision_id: {frontmatter_scalar(decision_id)}")
    if trace_id:
        frontmatter.append(f"trace_id: {frontmatter_scalar(trace_id)}")
    if tool_calls:
        frontmatter.append("tool_calls:")
        for tool_call in tool_calls:
            frontmatter.append(f"  - {json.dumps(tool_call, separators=(',', ':'))}")
    if constraints:
        frontmatter.append("constraints:")
        frontmatter.extend(f"  - {frontmatter_scalar(constraint)}" for constraint in constraints)
    if policies_applied:
        frontmatter.append("policies_applied:")
        frontmatter.extend(f"  - {frontmatter_scalar(policy)}" for policy in policies_applied)
    if provenance is not None:
        frontmatter.append(f"provenance: {json.dumps(provenance, separators=(',', ':'))}")
    frontmatter.append("---")

    body = [
        f"# {title}",
        "",
        "> Captured memory is rough intake for agent consolidation, not final Lore truth.",
        "",
        "## Observation",
        "",
        observation.strip(),
    ]
    if effective_source_task:
        body.extend(["", "## Source Task", "", effective_source_task])
    if sources:
        body.extend(["", "## Sources", ""])
        body.extend(f"- {source}" for source in sources)
    if source_paths:
        body.extend(["", "## Source Paths", ""])
        body.extend(f"- {path}" for path in source_paths)
    if source_urls:
        body.extend(["", "## Source URLs", ""])
        body.extend(f"- {url}" for url in source_urls)
    if evidence:
        body.extend(["", "## Evidence", "", evidence])
    if related_pages:
        body.extend(["", "## Related Pages", ""])
        body.extend(f"- [[{page_id}]]" for page_id in related_pages)
    if suggested_target_page:
        body.extend(["", "## Suggested Target", "", f"[[{suggested_target_page}]]"])

    return "\n".join([*frontmatter, "", *body]).rstrip() + "\n"


def parse_capture_date(value: str | None) -> date:
    if not value:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise InvalidPageId("capture_date must be an ISO date.") from exc


def title_from_observation(observation: str) -> str:
    first_line = next((line.strip() for line in observation.splitlines() if line.strip()), "")
    words = first_line.split()
    if not words:
        return "Captured Memory"
    title = " ".join(words[:8]).strip(" .,:;")
    return title or "Captured Memory"


def normalize_page_ids(page_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for page_id in page_ids:
        if not optional_string(page_id):
            continue
        cleaned = normalize_page_id(page_id)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def validate_page_id(page_id: str, field_name: str = "page_id") -> str:
    cleaned = page_id.strip()
    if not cleaned or not PAGE_ID_PATTERN.match(cleaned):
        raise InvalidPageId(f"Invalid page ID in {field_name}: {page_id!r}")
    return cleaned


def validate_source_url(url: str) -> bool:
    """Return True if the string looks like an HTTP(S) URL."""
    return url.strip().startswith(("http://", "https://"))


def normalize_optional_page_id(page_id: str | None) -> str | None:
    if not optional_string(page_id):
        return None
    return normalize_page_id(str(page_id))


def unique_page_id(repo: LoreRepository, base_page_id: str) -> str:
    page_id = normalize_page_id(base_page_id)
    if repo.read_page(page_id) is None:
        return page_id

    index = 2
    while True:
        candidate = normalize_page_id(f"{base_page_id}-{index}")
        if repo.read_page(candidate) is None:
            return candidate
        index += 1


def slugify(value: str) -> str:
    words = SLUG_PATTERN.findall(value.casefold())
    return "-".join(words[:10]) or "capture"


def frontmatter_list(values: list[str]) -> str:
    return "[" + ", ".join(frontmatter_scalar(value) for value in values) + "]"


def is_capture_page_id(page_id: str) -> bool:
    return page_id.startswith(("inbox/", "notes/"))
