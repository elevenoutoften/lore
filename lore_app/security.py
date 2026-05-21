"""Security utilities for Lore."""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


def sanitize_page_id(page_id: str) -> str:
    """Validate and normalize a page ID. Raises ValueError on invalid input."""
    if not page_id or not page_id.strip():
        raise ValueError("Page ID cannot be empty.")
    if "\x00" in page_id:
        raise ValueError("Page ID cannot contain null bytes.")
    if page_id.startswith("/"):
        raise ValueError("Page ID cannot be absolute.")
    if len(page_id) > 512:
        raise ValueError("Page ID too long (max 512 chars).")

    normalized = page_id.replace("\\", "/").strip()
    for segment in normalized.split("/"):
        if segment == "..":
            raise ValueError("Page ID cannot contain '..' segments.")
        if segment.startswith("."):
            raise ValueError("Page ID segments cannot start with '.'.")
    return normalized


def sanitize_content(content: str, max_length: int = 10_000_000) -> str:
    """Validate page content. Raises ValueError on invalid input."""
    if len(content) > max_length:
        raise ValueError(f"Content exceeds max length ({max_length} chars).")
    return content


class RateLimiter:
    """Simple in-memory fixed-window request limiter keyed by client identity."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str) -> bool:
        """Return True if request is allowed, False if rate limited."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            if len(self._requests) > 10_000:
                self._sweep_expired(cutoff)
            self._requests[key] = [request_time for request_time in self._requests[key] if request_time > cutoff]
            if len(self._requests[key]) >= self._max:
                return False
            self._requests[key].append(now)
        return True

    def _sweep_expired(self, cutoff: float) -> None:
        self._requests = defaultdict(
            list,
            {
                key: active
                for key, requests in self._requests.items()
                if (active := [request_time for request_time in requests if request_time > cutoff])
            },
        )
