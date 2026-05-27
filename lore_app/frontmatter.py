from __future__ import annotations

import json
from enum import Enum
from typing import Any


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, normalized

    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return {}, normalized

    frontmatter: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in lines[1:closing_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and current_list_key:
            value = parse_scalar(stripped[2:].strip())
            existing = frontmatter.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(value)
            continue

        if ":" not in line:
            current_list_key = None
            continue

        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            current_list_key = None
            continue
        if raw_value == "":
            frontmatter[key] = []
            current_list_key = key
        else:
            frontmatter[key] = parse_scalar(raw_value)
            current_list_key = None

    body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return frontmatter, body


def parse_frontmatter_only(content: str) -> dict[str, Any]:
    """Parse only the frontmatter block from content, skipping body parsing."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}

    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return {}

    frontmatter: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in lines[1:closing_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and current_list_key:
            value = parse_scalar(stripped[2:].strip())
            existing = frontmatter.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(value)
            continue

        if ":" not in line:
            current_list_key = None
            continue

        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            current_list_key = None
            continue
        if raw_value == "":
            frontmatter[key] = []
            current_list_key = key
        else:
            frontmatter[key] = parse_scalar(raw_value)
            current_list_key = None

    return frontmatter


def parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in inner.split(",")]
    if value.startswith("{") and value.endswith("}"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                return parsed
    if value.casefold() == "true":
        return True
    if value.casefold() == "false":
        return False
    if value.casefold() in ("null", "none", "~"):
        return None
    return value


def update_frontmatter(content: str, updates: dict[str, Any]) -> str:
    frontmatter, body = parse_frontmatter(content)
    merged = dict(frontmatter)
    for key, value in updates.items():
        merged[key] = value
    return serialize_markdown(merged, body)


def serialize_markdown(frontmatter: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.extend(serialize_frontmatter_field(key, value))
    lines.extend(["---", ""])
    return "\n".join(lines) + body.rstrip() + "\n"


def serialize_frontmatter_field(key: str, value: Any) -> list[str]:
    if isinstance(value, list):
        return [f"{key}:"] + [f"  - {frontmatter_scalar(item)}" for item in value]
    if isinstance(value, bool):
        return [f"{key}: {str(value).lower()}"]
    if value is None:
        return [f"{key}: null"]
    return [f"{key}: {frontmatter_scalar(value)}"]


def frontmatter_scalar(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return " ".join(str(value).split())
