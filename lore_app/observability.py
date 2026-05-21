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
    pages_served: int = 0
    searches: int = 0
    captures: int = 0
    mcp_requests: int = 0
    index_size: int = 0


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
