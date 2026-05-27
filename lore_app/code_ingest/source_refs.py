from __future__ import annotations

import re
from pathlib import Path

from .schemas import SourceReference

LANG_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".sh": "shell",
    ".dockerfile": "dockerfile",
    ".caddyfile": "caddyfile",
    ".service": "systemd",
}


def resolve_source_ref(raw: str, repo_root: str | Path | None = None) -> SourceReference:
    """Parse a raw source reference string into a structured SourceReference."""

    root = Path(repo_root) if repo_root else None
    file_path = raw
    line_start = None
    line_end = None
    symbol = None

    if "::" in file_path:
        file_path, symbol = file_path.split("::", 1)

    line_match = re.search(r":(\d+)(?:-(\d+))?$", file_path)
    if line_match:
        line_start = int(line_match.group(1))
        line_end = int(line_match.group(2)) if line_match.group(2) else None
        file_path = file_path[: line_match.start()]

    path = Path(file_path)
    language = _detect_language(path)
    if root and not path.is_absolute():
        resolved = root / path
        if resolved.exists():
            file_path = str(resolved)

    return SourceReference(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        language=language,
        symbol=symbol,
    )


def resolve_source_refs(raw_refs: list[str], repo_root: str | Path | None = None) -> list[SourceReference]:
    """Resolve a batch of source reference strings."""

    return [resolve_source_ref(raw_ref, repo_root) for raw_ref in raw_refs]


def _detect_language(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in LANG_MAP:
        return LANG_MAP[suffix]
    name = path.name.lower()
    if name in {"dockerfile", "caddyfile"}:
        return LANG_MAP[f".{name}"]
    return None
