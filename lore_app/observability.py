from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from threading import Lock

lore_logger = logging.getLogger("lore")


def log_request(method: str, path: str, status: int, duration_ms: float, actor: str = "") -> None:
    lore_logger.info(
        json.dumps(
            {
                "type": "request",
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": round(duration_ms, 1),
                "actor": actor,
            },
            sort_keys=True,
        )
    )


@dataclass
class Metrics:
    request_count: int = 0
    request_duration_ms: float = 0
    error_count: int = 0
    client_error_count: int = 0
    pages_served: int = 0
    searches: int = 0
    captures: int = 0
    mcp_requests: int = 0
    index_size: int = 0
    vector_chunks: int = 0
    vector_indexed_pages: int = 0
    vector_page_drift: int = 0
    vector_scope_drift: int = 0
    vector_pending_reindex: int = 0
    # Memory-quality gauges/counters (durable-memory health, not just HTTP/IO).
    consolidation_backlog: int = 0
    extraction_failures: int = 0
    apply_failures: int = 0
    recall_requests: int = 0
    recall_zero_results: int = 0


# Prometheus metric type per Metrics field. Anything not listed defaults to gauge.
_COUNTER_METRICS = frozenset(
    {
        "request_count",
        "error_count",
        "client_error_count",
        "pages_served",
        "searches",
        "captures",
        "mcp_requests",
        "extraction_failures",
        "apply_failures",
        "recall_requests",
        "recall_zero_results",
    }
)


class MetricsCollector:
    def __init__(self) -> None:
        self._metrics = defaultdict(Metrics)
        self._lock = Lock()
        self._started_at = time.monotonic()

    def record_request(self, path: str, method: str, status: int, duration_ms: float) -> None:
        with self._lock:
            metrics = self._metrics["global"]
            metrics.request_count += 1
            metrics.request_duration_ms += duration_ms
            if status >= 500:
                metrics.error_count += 1
            elif 400 <= status < 500:
                metrics.client_error_count += 1
            if method == "GET" and (path.startswith("/api/pages") or path.startswith("/pages/")):
                metrics.pages_served += 1
            if path.startswith("/api/search"):
                metrics.searches += 1
            if method == "POST" and path == "/api/capture":
                metrics.captures += 1
            if path.startswith("/mcp"):
                metrics.mcp_requests += 1

    def set_index_size(self, index_size: int) -> None:
        with self._lock:
            self._metrics["global"].index_size = index_size

    def increment_index_size(self, amount: int = 1) -> None:
        with self._lock:
            self._metrics["global"].index_size += amount

    def decrement_index_size(self, amount: int = 1) -> None:
        with self._lock:
            metrics = self._metrics["global"]
            metrics.index_size = max(0, metrics.index_size - amount)

    def set_vector_index_metrics(
        self,
        *,
        chunk_count: int,
        indexed_pages: int,
        page_drift: int,
        pending_reindex: int,
        scope_drift: int = 0,
    ) -> None:
        with self._lock:
            metrics = self._metrics["global"]
            metrics.vector_chunks = chunk_count
            metrics.vector_indexed_pages = indexed_pages
            metrics.vector_page_drift = page_drift
            metrics.vector_scope_drift = scope_drift
            metrics.vector_pending_reindex = pending_reindex

    def set_consolidation_backlog(self, count: int) -> None:
        with self._lock:
            self._metrics["global"].consolidation_backlog = max(0, count)

    def increment_extraction_failures(self, amount: int = 1) -> None:
        with self._lock:
            self._metrics["global"].extraction_failures += amount

    def increment_apply_failures(self, amount: int = 1) -> None:
        with self._lock:
            self._metrics["global"].apply_failures += amount

    def increment_recall_requests(self, amount: int = 1) -> None:
        with self._lock:
            self._metrics["global"].recall_requests += amount

    def increment_recall_zero_results(self, amount: int = 1) -> None:
        with self._lock:
            self._metrics["global"].recall_zero_results += amount

    def get_metrics(self) -> dict:
        with self._lock:
            metrics = asdict(self._metrics["global"])
        metrics["request_duration_ms"] = round(metrics["request_duration_ms"], 1)
        metrics["uptime_seconds"] = round(time.monotonic() - self._started_at)
        return metrics

    def reset(self) -> None:
        with self._lock:
            self._metrics.clear()
            self._started_at = time.monotonic()


def render_prometheus(metrics: dict) -> str:
    """Render a get_metrics() dict as Prometheus text exposition format.

    Every numeric metric is emitted with a ``lore_`` prefix plus HELP/TYPE
    metadata so a scraper can ingest recall hit-rate, consolidation backlog,
    and failure counters alongside the existing HTTP/IO and vector gauges.
    """
    lines: list[str] = []
    for key, value in metrics.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        name = f"lore_{key}"
        metric_type = "counter" if key in _COUNTER_METRICS else "gauge"
        lines.append(f"# HELP {name} Lore {key.replace('_', ' ')}.")
        lines.append(f"# TYPE {name} {metric_type}")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"
