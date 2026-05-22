from __future__ import annotations

from typing import Any

from .schemas import FrontmatterKindSpec, FrontmatterSpecResponse


FRONTMATTER_SPEC: dict[str, FrontmatterKindSpec] = {
    "project": FrontmatterKindSpec(
        kind="project",
        required=["title", "kind", "visibility", "summary", "owner"],
        optional=["tags", "sources", "status", "stale_after", "reviewed_at", "confidence", "epistemic_status"],
    ),
    "service": FrontmatterKindSpec(
        kind="service",
        required=["title", "kind", "visibility", "summary", "owner"],
        optional=["tags", "sources", "status", "stale_after", "reviewed_at", "confidence", "dependencies", "epistemic_status"],
    ),
    "decision": FrontmatterKindSpec(
        kind="decision",
        required=["title", "kind", "visibility", "summary", "status", "decided_at", "deciders"],
        optional=["tags", "sources", "alternatives", "stale_after", "reviewed_at", "confidence", "epistemic_status"],
    ),
    "runbook": FrontmatterKindSpec(
        kind="runbook",
        required=["title", "kind", "visibility", "summary"],
        optional=["tags", "sources", "owner", "status", "stale_after", "reviewed_at", "confidence", "steps", "epistemic_status"],
    ),
    "concept": FrontmatterKindSpec(
        kind="concept",
        required=["title", "kind", "visibility", "summary"],
        optional=["tags", "sources", "owner", "status", "stale_after", "reviewed_at", "confidence", "epistemic_status"],
    ),
    "capture": FrontmatterKindSpec(
        kind="capture",
        required=["title", "kind", "visibility", "captured_at", "confidence", "agent"],
        optional=[
            "tags",
            "sources",
            "source_task",
            "suggested_target_page",
            "related",
            "evidence",
            "promoted_to",
            "epistemic_status",
        ],
    ),
    "procedure": FrontmatterKindSpec(
        kind="procedure",
        required=[
            "title",
            "kind",
            "visibility",
            "summary",
            "trigger",
            "steps",
            "schema_version",
            "validated",
            "validated_at",
            "author",
        ],
        optional=[
            "tags",
            "sources",
            "owner",
            "status",
            "stale_after",
            "reviewed_at",
            "confidence",
            "preconditions",
            "postconditions",
            "error_handling",
            "epistemic_status",
        ],
    ),
    "page": FrontmatterKindSpec(
        kind="page",
        required=["title", "kind", "visibility"],
        optional=["summary", "tags", "sources", "owner", "status", "stale_after", "reviewed_at", "confidence", "epistemic_status"],
    ),
}


def get_frontmatter_spec() -> FrontmatterSpecResponse:
    all_fields = sorted({field for spec in FRONTMATTER_SPEC.values() for field in spec.required + spec.optional})
    return FrontmatterSpecResponse(specs=FRONTMATTER_SPEC, all_fields=all_fields)


def validate_frontmatter(kind: str, frontmatter: dict[str, Any]) -> list[str]:
    spec = FRONTMATTER_SPEC.get(kind) or FRONTMATTER_SPEC["page"]
    return [field for field in spec.required if not has_frontmatter_value(frontmatter, field)]


def has_frontmatter_value(frontmatter: dict[str, Any], field: str) -> bool:
    if field in {"author", "validated", "validated_at"}:
        return field in frontmatter
    value = frontmatter.get(field)
    if value is None:
        return False
    if isinstance(value, list):
        return bool(value)
    return bool(str(value).strip())
