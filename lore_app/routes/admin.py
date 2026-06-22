from __future__ import annotations

# ruff: noqa: B008
import platform
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, metadata, version
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from ..deps import get_audit_log, get_config, get_metrics
from ..observability import render_prometheus
from .api_keys import require_lore_key_admin

if TYPE_CHECKING:
    from ..audit import AuditLog
    from ..config import LoreConfig
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
        return "0.3.0b1"


@router.get("/healthz")
def healthz(metrics: MetricsCollector = Depends(get_metrics)) -> dict[str, Any]:
    return {"ok": True, "metrics": metrics.get_metrics()}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics_endpoint(metrics: MetricsCollector = Depends(get_metrics)) -> PlainTextResponse:
    """Prometheus-scrapeable metrics in text exposition format."""
    return PlainTextResponse(
        render_prometheus(metrics.get_metrics()),
        media_type="text/plain; version=0.0.4",
    )


@router.get("/healthz/config")
def healthz_config(request: Request) -> dict[str, Any]:
    config: LoreConfig = request.app.state.config
    return {
        "auth_mode": config.auth_mode,
        "trusted_headers": config.trusted_headers,
        "trusted_proxy_auth": config.trusted_proxy_auth,
    }


@router.get("/api/version")
def api_version() -> dict[str, str]:
    return {
        "name": package_name(),
        "version": package_version(),
        "python_version": platform.python_version(),
        "api_version": API_VERSION,
    }


@router.get("/api/whoami")
def api_whoami(request: Request, config: LoreConfig = Depends(get_config)) -> dict[str, str | None]:
    """Echo the caller's resolved actor and role.

    Any authenticated caller may read this. It exists so an agent can see the
    identity its captures are stamped with and that its default recall is scoped
    to — making tenancy visible instead of something to infer. The actor matches
    what ``stamp_capture_actor`` records, so ``whoami`` and capture attribution agree.
    """
    from ..route_utils import actor_from_request

    return {
        "actor": actor_from_request(request),
        "role": getattr(request.state, "lore_role", None),
        "auth_mode": config.auth_mode,
    }


@router.get("/api/config")
def api_config(
    _admin: None = Depends(require_lore_key_admin),
    config: LoreConfig = Depends(get_config),
):
    return config.to_dict()


@router.get("/api/audit")
def api_audit(
    _admin: None = Depends(require_lore_key_admin),
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
