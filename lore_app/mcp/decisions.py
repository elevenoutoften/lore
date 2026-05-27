from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ..frontmatter import frontmatter_scalar

SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def build_decision_markdown(
    *,
    title: str,
    summary: str,
    context: str,
    decision: str,
    consequences: str,
    deciders: list[str],
    status: str,
) -> str:
    frontmatter = [
        "---",
        f"title: {frontmatter_scalar(title)}",
        "kind: decision",
        "visibility: internal",
        f"summary: {frontmatter_scalar(summary)}",
        f"status: {frontmatter_scalar(status)}",
        f"decided_at: {date.today().isoformat()}",
    ]
    if deciders:
        frontmatter.append("deciders:")
        frontmatter.extend(f"  - {frontmatter_scalar(decider)}" for decider in deciders)
    else:
        frontmatter.append("deciders:")
        frontmatter.append("  - unknown")
    frontmatter.append("---")
    body = [
        f"# {title}",
        "",
        "## Context",
        context.strip(),
        "",
        "## Decision",
        decision.strip(),
        "",
        "## Consequences",
        consequences.strip(),
    ]
    return "\n".join([*frontmatter, "", *body]).rstrip() + "\n"


def build_procedure_markdown(
    *,
    title: str,
    summary: str,
    trigger: str,
    steps: list[str],
    preconditions: list[str],
    postconditions: list[str],
    error_handling: str,
    author: str = "",
    schema_version: str = "1.0",
) -> str:
    frontmatter = [
        "---",
        f"title: {frontmatter_scalar(title)}",
        "kind: procedure",
        "visibility: internal",
        f"summary: {frontmatter_scalar(summary)}",
        f"trigger: {frontmatter_scalar(trigger)}",
    ]
    if preconditions:
        frontmatter.append("preconditions:")
        frontmatter.extend(f"  - {frontmatter_scalar(item)}" for item in preconditions)
    frontmatter.append("steps:")
    frontmatter.extend(f"  - {frontmatter_scalar(step)}" for step in steps)
    if postconditions:
        frontmatter.append("postconditions:")
        frontmatter.extend(f"  - {frontmatter_scalar(item)}" for item in postconditions)
    if error_handling:
        frontmatter.append(f"error_handling: {frontmatter_scalar(error_handling)}")
    frontmatter.extend(
        [
            f"schema_version: {frontmatter_scalar(schema_version)}",
            "author: " + (frontmatter_scalar(author) if author else '""'),
            "validated: false",
            "validated_at: null",
            "sources:",
            "  - mcp:lore_create_procedure",
            "status: draft",
            "---",
        ]
    )

    body = [
        f"# {title}",
        "",
        "## Trigger",
        trigger.strip(),
        "",
    ]
    if preconditions:
        body.extend(["## Preconditions", *[f"- {item}" for item in preconditions], ""])
    body.extend(["## Steps", ""])
    body.extend(f"{index}. {step}" for index, step in enumerate(steps, 1))
    if postconditions:
        body.extend(["", "## Postconditions", *[f"- {item}" for item in postconditions]])
    if error_handling:
        body.extend(["", "## Error Handling", error_handling.strip()])
    return "\n".join([*frontmatter, "", *body]).rstrip() + "\n"


def export_procedure_skill(page: Any) -> str:
    steps = string_arguments(page.frontmatter.get("steps"))
    trigger = optional_string(page.frontmatter.get("trigger")) or ""
    preconditions = string_arguments(page.frontmatter.get("preconditions"))
    postconditions = string_arguments(page.frontmatter.get("postconditions"))
    error_handling = optional_string(page.frontmatter.get("error_handling")) or ""
    skill_name = page.id.replace("/", "-")
    yaml_name = skill_name if SKILL_NAME_PATTERN.fullmatch(skill_name) else json.dumps(skill_name)

    return f"""---
name: {yaml_name}
description: {page.summary or page.title}
---

# {page.title}

{page.summary or ""}

## Trigger
{trigger}

## Preconditions
{chr(10).join(f"- {p}" for p in preconditions) if preconditions else "None"}

## Steps
{chr(10).join(f"{i}. {s}" for i, s in enumerate(steps, 1)) if steps else "None"}

## Postconditions
{chr(10).join(f"- {p}" for p in postconditions) if postconditions else "None"}

## Error Handling
{error_handling or "See page body for details."}

## Source
Exported from [Lore]({page.id}).
"""


def optional_string(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def string_arguments(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []
