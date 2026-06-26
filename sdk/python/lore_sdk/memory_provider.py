from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, ClassVar


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
    _BACKOFF_DELAYS: ClassVar[list[float]] = [1.0, 2.0, 4.0]
    _CIRCUIT_FAILURE_THRESHOLD = 5
    _CIRCUIT_RECOVERY_SECONDS = 60.0

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LORE_BASE_URL", "http://localhost:8078")).rstrip("/")
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
        *,
        task_id: str | None = None,
        decision_id: str | None = None,
        trace_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        constraints: list[str] | None = None,
        policies_applied: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> str:
        """Write a memory capture to Lore.

        Args:
            memory_text: The raw observation or memory to persist.
            agent_name: Agent name (e.g. "nyx", "phoebe"). Used for notes namespace.
            namespace: "inbox" for shared, "notes" for agent-scoped.
            metadata: Optional frontmatter fields such as confidence, source_task, tags.
            lane: Retrieval lane — project, procedural, ops, companion, draft.
            actor: Compatibility hint only; authenticated servers replace it with the token actor.
            task_id: Source task ID (e.g. "flow_000123").
            decision_id: Linked decision page ID.
            trace_id: Reasoning trace correlation ID.
            tool_calls: Tool call records from the capturing session.
            constraints: Constraints that applied during capture.
            policies_applied: Policy IDs that were enforced.
            provenance: Structured provenance using Lore's ProvenanceRef fields.
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
        if task_id:
            payload["task_id"] = task_id
        if decision_id:
            payload["decision_id"] = decision_id
        if trace_id:
            payload["trace_id"] = trace_id
        if tool_calls:
            payload["tool_calls"] = tool_calls
        if constraints:
            payload["constraints"] = constraints
        if policies_applied:
            payload["policies_applied"] = policies_applied
        if provenance:
            payload["provenance"] = provenance

        result = self._post_with_retry("/api/memory/capture", payload)
        return str(result.get("capture_id") or result.get("page", {}).get("id"))

    def recall(
        self,
        query: str | None = None,
        *,
        subject: str | None = None,
        lane: str | None = None,
        actor: str | None = None,
        min_strength: float = 0.0,
        limit: int = 20,
        record_access: bool = False,
        cross_actor: bool = False,
    ) -> list[dict[str, Any]]:
        """Recall durable memory ranked by recency/salience-weighted score.

        Returns the ranked claims, each with a ``recall_score`` and a
        ``recall_signals`` breakdown (strength, recency, salience, relevance).

        Args:
            query: Optional query to bias ranking by lexical relevance.
            subject: Filter to claims about an exact normalized subject.
            lane: Filter to a retrieval lane.
            actor: Filter to claims produced by a specific agent.
            min_strength: Minimum ledger strength to consider.
            limit: Max claims to return.
            record_access: Explicitly stamp access on returned claims. Defaults
                false so reads are idempotent.
            cross_actor: Admin-only flag to explicitly recall outside the
                authenticated actor scope.
        """
        result = self.recall_response(
            query,
            subject=subject,
            lane=lane,
            actor=actor,
            min_strength=min_strength,
            limit=limit,
            record_access=record_access,
            cross_actor=cross_actor,
        )
        claims = result.get("claims")
        return claims if isinstance(claims, list) else []

    def recall_response(
        self,
        query: str | None = None,
        *,
        subject: str | None = None,
        lane: str | None = None,
        actor: str | None = None,
        min_strength: float = 0.0,
        limit: int = 20,
        record_access: bool = False,
        cross_actor: bool = False,
    ) -> dict[str, Any]:
        """Like :meth:`recall` but returns the full response envelope.

        Where ``recall`` returns just the ranked claims list, this returns the
        whole response — ``claims`` plus the self-diagnosing ``count``,
        ``pending_captures``, ``hint`` and ``weights`` fields. Use it when you
        need to know *why* a recall came back empty (memory not consolidated yet,
        scoped to another actor, or genuinely absent).
        """
        params: dict[str, str] = {"limit": str(int(limit))}
        if query:
            params["query"] = query
        if subject:
            params["subject"] = subject
        if lane:
            params["lane"] = lane
        if actor:
            params["actor"] = actor
        if min_strength:
            params["min_strength"] = str(float(min_strength))
        params["record_access"] = "true" if record_access else "false"
        if cross_actor:
            params["cross_actor"] = "true"
        path = "/api/memory/recall?" + urllib.parse.urlencode(params)
        result = self._send_with_retry("GET", path)
        return result if isinstance(result, dict) else {}

    def context(
        self,
        query: str | None = None,
        *,
        subject: str | None = None,
        lane: str | None = None,
        actor: str | None = None,
        min_strength: float = 0.0,
        limit: int = 10,
        max_tokens: int = 900,
        max_chars: int = 4000,
        include_recall: bool = True,
        include_rag: bool = True,
        rag_expand_hops: int = 1,
        cross_actor: bool = False,
    ) -> dict[str, Any]:
        """Return a deterministic prompt-ready memory context block.

        The server assembles recall claims and optional RAG page hits into
        bounded markdown with inline citations. No LLM call is made.
        """
        params: dict[str, str] = {
            "limit": str(int(limit)),
            "max_tokens": str(int(max_tokens)),
            "max_chars": str(int(max_chars)),
            "include_recall": "true" if include_recall else "false",
            "include_rag": "true" if include_rag else "false",
            "rag_expand_hops": str(int(rag_expand_hops)),
        }
        if query:
            params["query"] = query
        if subject:
            params["subject"] = subject
        if lane:
            params["lane"] = lane
        if actor:
            params["actor"] = actor
        if min_strength:
            params["min_strength"] = str(float(min_strength))
        if cross_actor:
            params["cross_actor"] = "true"
        path = "/api/memory/context?" + urllib.parse.urlencode(params)
        result = self._send_with_retry("GET", path)
        return result if isinstance(result, dict) else {}

    def to_openai(self, query: str | None = None, **options: Any) -> list[dict[str, str]]:
        """Return the context block as an OpenAI-ready system message."""
        response = self.context(query, **options)
        return [{"role": "system", "content": str(response.get("context") or "")}]

    def to_anthropic(self, query: str | None = None, **options: Any) -> list[dict[str, str]]:
        """Return the context block as an Anthropic-ready text content block."""
        response = self.context(query, **options)
        return [{"type": "text", "text": str(response.get("context") or "")}]

    def acknowledge_recall(self, candidate_ids: list[str]) -> dict[str, Any]:
        """Acknowledge recalled claims that were used, boosting salience."""
        result = self._post_with_retry("/api/memory/recall/ack", {"candidate_ids": candidate_ids})
        return result if isinstance(result, dict) else {}

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
        return str(result.get("id") or result.get("page_id") or result.get("page", {}).get("id"))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _post_with_retry(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._send_with_retry("POST", path, payload)

    def _send_with_retry(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._check_circuit()

        last_exception: Exception | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                result = self._request(method, path, payload)
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
            raise CircuitOpenError(f"Circuit breaker is open. Retry after {self._circuit_open_until:.0f}.")
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
