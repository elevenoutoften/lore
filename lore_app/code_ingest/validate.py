"""Validate code-ingest source directories against configured roots and limits."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import LoreConfig


class IngestValidationError(ValueError):
    """Raised when a source_dir fails validation."""


def validate_source_dir(source_dir: str | Path, config: LoreConfig) -> Path:
    """Validate and resolve a source_dir against configured roots and limits.

    Returns the resolved Path if valid.
    Raises IngestValidationError with a clear error message if invalid.
    """
    resolved = Path(source_dir).resolve()

    # Roots must be configured
    if not config.code_ingest_roots:
        raise IngestValidationError(
            "Code ingest is disabled. Set LORE_CODE_INGEST_ROOTS to enable."
        )

    # Must be under an allowed root (resolve prevents symlink escapes)
    if not any(resolved == root or resolved.is_relative_to(root) for root in config.code_ingest_roots):
        raise IngestValidationError(
            f"source_dir '{source_dir}' is outside the allowed roots. "
            f"Allowed roots: {', '.join(str(r) for r in config.code_ingest_roots)}"
        )

    # Must exist and be a directory
    if not resolved.is_dir():
        raise IngestValidationError(
            f"source_dir '{source_dir}' does not exist or is not a directory."
        )

    # Depth limit (count parent levels from root)
    for root in config.code_ingest_roots:
        if resolved == root or resolved.is_relative_to(root):
            rel = resolved.relative_to(root)
            if len(rel.parts) > config.code_ingest_max_depth:
                raise IngestValidationError(
                    f"source_dir exceeds max depth ({config.code_ingest_max_depth}). "
                    f"Path depth from root: {len(rel.parts)}"
                )
            break

    # File count and total size limits
    py_files = list(resolved.rglob("*.py"))
    if len(py_files) > config.code_ingest_max_files:
        raise IngestValidationError(
            f"source_dir contains {len(py_files)} Python files, exceeding the "
            f"limit of {config.code_ingest_max_files}. Set LORE_CODE_INGEST_MAX_FILES to increase."
        )

    total_size = sum(f.stat().st_size for f in py_files if f.is_file())
    if total_size > config.code_ingest_max_total_bytes:
        raise IngestValidationError(
            f"source_dir contains {total_size / (1024*1024):.1f} MiB of Python files, "
            f"exceeding the limit of {config.code_ingest_max_total_bytes / (1024*1024):.0f} MiB. "
            f"Set LORE_CODE_INGEST_MAX_TOTAL_BYTES to increase."
        )

    return resolved


def validate_service_id(service_id: str) -> str:
    """Validate service_id as a safe identifier (alphanumeric, hyphens, underscores, slashes)."""
    if not re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_/.-]*$", service_id):
        raise IngestValidationError(
            f"service_id '{service_id}' contains invalid characters. "
            "Only alphanumeric, underscores, hyphens, dots, and slashes are allowed."
        )
    if ".." in service_id:
        raise IngestValidationError("service_id must not contain '..'")
    return service_id
