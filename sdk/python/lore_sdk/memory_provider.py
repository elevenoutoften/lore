from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class CircuitOpenError(RuntimeError):
    """Raised when the circuit breaker is open and requests are blocked."""

    pass


class MemoryProviderError(RuntimeError):
    pass


class MemoryProvider:
    """Standalone durable memory provider for Lore captures.

    Zero-dependency (stdlib only) client that wraps the Lore capture API with
    auth handling, exponential-backoff retries, and a simple circuit breaker.
    Can be used by Hermes, Nyx, Phoebe without importing lore_app internals.

    Usage::

        from lore_sdk.memory_provider import MemoryProvider

        provider = MemoryProvider()
        capture_id = provider.capture(
            memory_text="Deployed v2.3 to staging.",
            agent_name="hermes",
            namespace="inbox",
            metadata={"confidence": "high", "source_task": "flow_000123"},
        )
    """

    _MAX_RETRIES = 3
    _BACKOFF_DELAYS = [1.0, 2.0, 4.0]
    _CIRCUIT_FAILURE_THRESHOLD = 5
    _CIRCUIT_RECOVERY_SECONDS = 60.0

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LORE_BASE_URL", "http://localhost:8000")).rstrip("/")
        self.api_key = (api_key or os.environ.get("LORE_API_KEY", "")).strip()
        self.timeout = timeout

        # circuit-breaker state
        self._consecutive_failures = 0
        self._circuit_open_until: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture(
        self,
        memory_text: str,
        agent_name: str | None = None,
        namespace: str = "inbox",
        metadata: dict[str, Any] | None = None,
        lane: str | None = None,
        actor: str | None = None,
    ) -> str:
        """Write a memory capture to Lore.

        Args:
            memory_text: The raw observation or memory to persist.
            agent_name: Agent name (e.g. "nyx", "phoebe"). Used for notes namespace.
            namespace: "inbox" for shared, "notes" for agent-scoped.
            metadata: Optional frontmatter fields such as confidence, source_task, tags.
            lane: Retrieval lane — project, procedural, ops, companion, draft.
            actor: Agent name for provenance (defaults to agent_name if not set).
        """
        payload: dict[str, Any] = {
            "text": memory_text,
            "namespace": namespace,
        }
        if agent_name:
            payload["agent_name"] = agent_name
        if lane:
            payload["lane"] = lane
        if actor or agent_name:
            payload["actor"] = actor or agent_name
        if metadata:
            payload["metadata"] = metadata

        result = self._post_with_retry("/api/memory/capture", payload)
        return str(result.get("capture_id") or result.get("page", {}).get("id"))

    def page_promote(
        self,
        capture_id: str,
        target_page_id: str | None = None,
        content: str | None = None,
    ) -> str:
        """Promote a capture to a canonical Lore page.

        Args:
            capture_id: The capture page_id to promote.
            target_page_id: Optional canonical page ID. Falls back to capture's suggested_target_page.
            content: Optional explicit Markdown content for the target page.

        Returns:
            The promoted canonical page_id.
        """
        payload: dict[str, Any] = {}
        if target_page_id:
            payload["target_page_id"] = target_page_id
        if content:
            payload["content"] = content

        result = self._post_with_retry(
            f"/api/captures/{urllib.parse.quote(capture_id, safe='/')}/promote",
            payload,
        )
        return str(result.get("page_id") or result.get("page", {}).get("id"))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _post_with_retry(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_circuit()

        last_exception: Exception | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                result = self._request("POST", path, payload)
                self._on_success()
                return result
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_exception = exc
                if attempt < self._MAX_RETRIES:
                    delay = self._BACKOFF_DELAYS[attempt]
                    time.sleep(delay)
                else:
                    break

        self._on_failure()
        raise MemoryProviderError(
            f"Lore memory provider failed after {self._MAX_RETRIES + 1} attempts: {last_exception}"
        ) from last_exception

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        body = None
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _check_circuit(self) -> None:
        if self._circuit_open_until is None:
            return
        if time.monotonic() < self._circuit_open_until:
            raise CircuitOpenError(
                f"Circuit breaker is open. Retry after {self._circuit_open_until:.0f}."
            )
        # recovery window elapsed — try again
        self._circuit_open_until = None
        self._consecutive_failures = 0

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = None

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_open_until = time.monotonic() + self._CIRCUIT_RECOVERY_SECONDS
