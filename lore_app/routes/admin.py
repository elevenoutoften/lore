from __future__ import annotations

import platform
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, metadata, version
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..audit import AuditLog
from ..config import LoreConfig
from ..deps import get_audit_log, get_config, get_metrics
from ..observability import MetricsCollector

PROJECT_NAME = "lore"
API_VERSION = "1"

router = APIRouter()


def package_name() -> str:
    try:
        return metadata(PROJECT_NAME)["Name"]
    except PackageNotFoundError:
        return PROJECT_NAME


def package_version() -> str:
    try:
        return version(PROJECT_NAME)
    except PackageNotFoundError:
        return "0.2.0"


@router.get("/healthz")
def healthz(metrics: MetricsCollector = Depends(get_metrics)) -> dict[str, Any]:
    return {"ok": True, "metrics": metrics.get_metrics()}


@router.get("/api/version")
def api_version() -> dict[str, str]:
    return {
        "name": package_name(),
        "version": package_version(),
        "python_version": platform.python_version(),
        "api_version": API_VERSION,
    }


@router.get("/api/config")
def api_config(config: LoreConfig = Depends(get_config)):
    return config.to_dict()


@router.get("/api/audit")
def api_audit(
    page_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    audit_log: AuditLog = Depends(get_audit_log),
):
    return [asdict(entry) for entry in audit_log.query(page_id=page_id, actor=actor, since=since, limit=limit)]


@router.get("/api/semantics")
def api_semantics():
    return {
        "confidence_levels": {
            "low": "Information is unverified or from a single source. Treat as hypothesis.",
            "medium": "Information is partially verified or from reliable sources. Treat as probable.",
            "high": "Information is well-established and cross-referenced. Treat as fact.",
            "unknown": "Confidence has not been assessed.",
        },
        "status_values": {
            "draft": "Initial capture awaiting consolidation.",
            "review": "Queued for focused agent consolidation or manual audit.",
            "accepted": "Promoted or incorporated as canonical knowledge.",
            "deprecated": "No longer current. Kept for historical reference.",
            "stub": "Auto-created placeholder awaiting real content.",
        },
        "visibility_levels": {
            "internal": "Visible to authenticated agents and team members.",
            "public": "Visible to unauthenticated readers.",
        },
    }
