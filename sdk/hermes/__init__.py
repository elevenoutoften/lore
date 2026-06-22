"""Lore memory plugin — MemoryProvider for the Lore knowledge backend.

Connects Hermes to a Lore server (default http://localhost:8078) and
exposes search, read, capture, recall, and acknowledgement tools.  Lifecycle hooks mirror
built-in memory writes and extract insights before context compression.

All API calls go through the canonical ``lore_sdk`` clients — no local HTTP code.

Configuration is via environment variables only:
  LORE_BASE_URL  — Lore server URL (default: http://localhost:8078)
  LORE_API_KEY   — Lore bearer token
  LORE_SDK_PATH  — override path to the directory containing lore_sdk/
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Make lore_sdk importable
# ---------------------------------------------------------------------------

# When deployed as a symlink under ~/.hermes/plugins/lore/, __file__
# resolves to the real path inside the Lore repo.  From
# sdk/hermes/__init__.py, the SDK is at
# sdk/python/.
_SDK_PATH = Path(os.environ.get("LORE_SDK_PATH", "") or str(Path(__file__).resolve().parent.parent / "python"))
# If LORE_SDK_PATH points to the lore_sdk package itself, use its parent.
if _SDK_PATH.name == "lore_sdk" and _SDK_PATH.is_dir():
    _SDK_PATH = _SDK_PATH.parent
# Append (do not prepend) to avoid shadowing installed packages.
if _SDK_PATH.is_dir() and str(_SDK_PATH) not in sys.path:
    sys.path.append(str(_SDK_PATH))

from agent.memory_provider import MemoryProvider as HermesMemoryProvider  # noqa: E402
from lore_sdk import LoreClient  # noqa: E402
from lore_sdk import MemoryProvider as LoreSdkMemoryProvider  # noqa: E402
from lore_sdk.exceptions import LoreError  # noqa: E402
from lore_sdk.memory_provider import MemoryProviderError  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

LORE_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "lore_search",
    "description": (
        "Search the Lore knowledge base for relevant context. "
        "Returns page titles, summaries, and snippets. "
        "Use this to find project docs, decisions, runbooks, and concepts "
        "before making assumptions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "kind": {
                "type": "string",
                "description": "Filter by page kind (concept, decision, runbook, service, project, page, capture)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10)",
            },
        },
        "required": ["query"],
    },
}

LORE_READ_SCHEMA: dict[str, Any] = {
    "name": "lore_read",
    "description": (
        "Read a full Lore page by ID. Returns the page content in Markdown "
        "with frontmatter. Use wikilink IDs (e.g. 'services/muse/architecture') "
        "not titles."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page_id": {
                "type": "string",
                "description": "Lore page ID (slug path, e.g. 'services/muse/architecture')",
            },
        },
        "required": ["page_id"],
    },
}

LORE_CAPTURE_SCHEMA: dict[str, Any] = {
    "name": "lore_capture",
    "description": (
        "Capture an observation or insight into Lore durable memory. "
        "The capture is consolidated into ranked claims for later recall. "
        "Use for important discoveries, decisions, or facts worth remembering "
        "across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "observation": {
                "type": "string",
                "description": "The observation or insight to capture",
            },
            "title": {
                "type": "string",
                "description": "Short title for the capture",
            },
            "namespace": {
                "type": "string",
                "enum": ["inbox", "notes"],
                "description": "inbox for quick captures, notes for agent observations (default: notes)",
            },
            "lane": {
                "type": "string",
                "enum": ["project", "procedural", "ops", "companion", "draft"],
                "description": "Optional retrieval lane for filtering later recall",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high", "unknown"],
                "description": "Confidence level (default: unknown)",
            },
            "source_task": {
                "type": "string",
                "description": "Source task or flow ID",
            },
            "related_pages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Related Lore page IDs",
            },
        },
        "required": ["observation"],
    },
}

LORE_RECALL_SCHEMA: dict[str, Any] = {
    "name": "lore_recall",
    "description": (
        "Recall durable Lore claims ranked by relevance, recency, strength, and salience. "
        "Returns candidate IDs for lore_ack plus a hint when no claims are ready."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional natural-language query to bias ranking",
            },
            "subject": {
                "type": "string",
                "description": "Filter to an exact normalized subject",
            },
            "lane": {
                "type": "string",
                "enum": ["project", "procedural", "ops", "companion", "draft"],
                "description": "Filter to a retrieval lane",
            },
            "min_strength": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Minimum claim strength (default 0)",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Maximum claims to return (default 20)",
            },
        },
    },
}

LORE_ACK_SCHEMA: dict[str, Any] = {
    "name": "lore_ack",
    "description": (
        "Acknowledge recalled Lore claims that were actually used. "
        "This boosts their salience without changing recency or decay age."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "candidate_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Candidate IDs returned by lore_recall",
            },
        },
        "required": ["candidate_ids"],
    },
}

ALL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    LORE_SEARCH_SCHEMA,
    LORE_READ_SCHEMA,
    LORE_CAPTURE_SCHEMA,
    LORE_RECALL_SCHEMA,
    LORE_ACK_SCHEMA,
]


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------


class LoreMemoryProvider(HermesMemoryProvider):
    """Lore knowledge backend — pages, wikilinks, search, and capture pipeline.

    Delegates all HTTP I/O to ``lore_sdk.LoreClient`` and the canonical
    ``lore_sdk.MemoryProvider``.
    """

    def __init__(self) -> None:
        self._session_id: str = ""
        self._agent_name: str = "hermes"
        self._agent_context: str = ""
        self._connected: bool = False
        self._client: LoreClient | None = None
        self._memory_client: LoreSdkMemoryProvider | None = None
        # Prefetch cache: {query: result_string}
        self._prefetch_cache: dict[str, str] = {}
        self._prefetch_lock = threading.Lock()

    # -- Identity / availability ---------------------------------------------

    @property
    def name(self) -> str:
        return "lore"

    def is_available(self) -> bool:
        """True if LORE_API_KEY is set (write credentials required)."""
        return bool(os.environ.get("LORE_API_KEY"))

    # -- Config schema (for ``hermes memory setup``) -------------------------

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "base_url",
                "description": "Lore server URL",
                "default": "http://localhost:8078",
                "env_var": "LORE_BASE_URL",
            },
            {
                "key": "api_key",
                "description": "Lore API key (Bearer token)",
                "secret": True,
                "env_var": "LORE_API_KEY",
            },
        ]

    # -- Core lifecycle ------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._agent_name = kwargs.get("agent_identity", "hermes")
        self._agent_context = kwargs.get("agent_context", "")
        self._connected = False

        try:
            base_url = os.environ.get("LORE_BASE_URL", "http://localhost:8078")
            api_key = os.environ.get("LORE_API_KEY", "")
            self._client = LoreClient(
                base_url=base_url,
                auth_token=api_key,
            )
            self._memory_client = LoreSdkMemoryProvider(base_url=base_url, api_key=api_key)
            # Warm-up: verify connectivity with a lightweight request.
            self._client.list_pages(limit=1)
            self._connected = True
            logger.info(
                "Lore provider initialized (session=%s, agent=%s, context=%s)",
                session_id,
                self._agent_name,
                self._agent_context,
            )
        except Exception as exc:
            # Keep the client for lazy reconnection — don't set to None.
            logger.warning("Lore provider warm-up failed: %s", exc)

    def _try_reconnect(self) -> bool:
        """Attempt to reconnect if currently disconnected. Returns True if connected."""
        if self._connected:
            return True
        if not self._client:
            return False
        try:
            self._client.list_pages(limit=1)
            self._connected = True
            logger.info("Lore provider reconnected")
            return True
        except Exception as exc:
            logger.debug("Lore provider reconnect failed: %s", exc)
            return False

    def system_prompt_block(self) -> str:
        if not self._connected:
            return ""
        return (
            "Lore knowledge backend connected. "
            "Use lore_search to find relevant context, "
            "lore_read to retrieve pages, and "
            "lore_capture to persist important observations, lore_recall to retrieve "
            "ranked durable claims, and lore_ack after using recalled claims. "
            "Lore pages are structured knowledge — concepts, decisions, runbooks, "
            "and project docs linked by wikilinks."
        )

    def shutdown(self) -> None:
        # Nothing to flush — Lore writes are synchronous.
        pass

    # -- Tool surface --------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return ALL_TOOL_SCHEMAS

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        if not self._client or not self._memory_client:
            return json.dumps({"error": "Lore provider not configured"})

        # Lazy reconnect if disconnected.
        if not self._connected and not self._try_reconnect():
            return json.dumps({"error": "Lore provider not connected"})

        try:
            if tool_name == "lore_search":
                kind = args.get("kind")
                limit = args.get("limit", 10)
                # Overfetch when filtering by kind to avoid under-returning.
                fetch_limit = limit * 3 if kind else limit
                result = self._client.search(args["query"], limit=fetch_limit)
                if kind and isinstance(result, dict):
                    hits = result.get("hits", [])
                    result["hits"] = [h for h in hits if h.get("page", h).get("kind") == kind][:limit]
            elif tool_name == "lore_read":
                result = self._client.get_page(args["page_id"])
            elif tool_name == "lore_capture":
                metadata: dict[str, Any] = {"tags": ["agent-memory"]}
                if args.get("title"):
                    metadata["title"] = args["title"]
                if args.get("confidence"):
                    metadata["confidence"] = args["confidence"]
                if args.get("related_pages"):
                    metadata["related_pages"] = args["related_pages"]
                capture_id = self._memory_client.capture(
                    memory_text=args["observation"],
                    agent_name=self._agent_name,
                    namespace=args.get("namespace", "notes"),
                    metadata=metadata,
                    lane=args.get("lane"),
                    task_id=args.get("source_task"),
                )
                result = {"capture_id": capture_id}
            elif tool_name == "lore_recall":
                result = self._memory_client.recall_response(
                    args.get("query"),
                    subject=args.get("subject"),
                    lane=args.get("lane"),
                    min_strength=args.get("min_strength", 0.0),
                    limit=args.get("limit", 20),
                )
            elif tool_name == "lore_ack":
                result = self._memory_client.acknowledge_recall(args["candidate_ids"])
            else:
                return json.dumps({"error": f"Unknown Lore tool: {tool_name}"})
            return json.dumps(result)
        except LoreError as exc:
            logger.error("Lore tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": exc.message, "status_code": exc.status_code})
        except MemoryProviderError as exc:
            logger.error("Lore memory tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            logger.error("Lore tool %s unexpected error: %s", tool_name, exc)
            return json.dumps({"error": str(exc)})

    # -- Prefetch (context recall) -------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return cached prefetch results, or empty if not yet available."""
        with self._prefetch_lock:
            return self._prefetch_cache.get(query, "")

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Background search — caches results for the next prefetch() call."""
        if not query or not self._client:
            return

        def _search():
            try:
                prefetch_client = LoreClient(
                    base_url=self._client.base_url,
                    auth_token=self._client.auth_token,
                    timeout=10,
                )
                if not self._connected:
                    prefetch_client.list_pages(limit=1)
                    self._connected = True
                fetch_limit = 5
                results = prefetch_client.search(query, limit=fetch_limit)
                items = results if isinstance(results, list) else results.get("hits", [])
                if not items:
                    return
                lines = ["[LORE CONTEXT]"]
                for item in items:
                    page = item.get("page", item)
                    title = page.get("title", "Untitled")
                    kind = page.get("kind", "")
                    summary = page.get("summary") or page.get("snippet") or page.get("content", "")
                    if len(summary) > 300:
                        summary = summary[:297] + "..."
                    kind_label = f" ({kind})" if kind else ""
                    lines.append(f"- {title}{kind_label}: {summary}")
                lines.append("[/LORE CONTEXT]")
                with self._prefetch_lock:
                    self._prefetch_cache[query] = "\n".join(lines)
            except Exception as exc:
                logger.debug("Lore queue_prefetch failed: %s", exc)

        thread = threading.Thread(target=_search, daemon=True, name="lore-prefetch")
        thread.start()

    # -- Turn sync (no-op — Lore captures are explicit) ----------------------

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        pass

    # -- Optional hooks ------------------------------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Mirror built-in memory writes to Lore as captures."""
        # Skip writes for non-primary contexts (cron, subagent, etc.)
        if self._agent_context and self._agent_context != "primary":
            return
        if not self._memory_client:
            return
        # Lazy reconnect if needed.
        if not self._connected and not self._try_reconnect():
            return
        try:
            meta = metadata or {}
            source_task = meta.get("task_id") or meta.get("source_task") or meta.get("session_id")
            # Preserve provenance metadata.
            # Lore sanitizes the tags field on captures, keeping only known tags.
            # Use structured capture fields (source_task) for primary provenance
            # and add a compact provenance line to the body for the rest.
            prov_parts = []
            for key in ("write_origin", "execution_context", "platform", "tool_name"):
                if meta.get(key):
                    prov_parts.append(f"{key}={meta[key]}")
            provenance_line = f"[{' '.join(prov_parts)}]" if prov_parts else ""
            capture_metadata: dict[str, Any] = {
                "title": f"Memory: {target}/{action}",
                "tags": ["agent-memory"],
            }
            hermes_builtin_targets = {"memory", "user"}
            if target in hermes_builtin_targets and not meta.get("lore_worthy"):
                logger.debug(
                    "Skipping Lore capture for built-in target: %s/%s",
                    target,
                    action,
                )
                return
            if "/" in target:
                capture_metadata["suggested_target_page"] = target
                capture_metadata["related_pages"] = [target]
            self._memory_client.capture(
                memory_text=f"{content}\n\n{provenance_line}" if provenance_line else content,
                agent_name=self._agent_name,
                namespace="notes",
                metadata=capture_metadata,
                task_id=str(source_task) if source_task else None,
            )
        except Exception as exc:
            logger.debug("Lore on_memory_write mirror failed: %s", exc)

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        """Placeholder — future enhancement can extract key facts before compression."""
        return ""


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register Lore as a memory provider plugin."""
    ctx.register_memory_provider(LoreMemoryProvider())
