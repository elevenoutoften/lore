from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..analytics import GraphAnalytics
from ..capture import (
    CaptureNotFoundError,
    build_capture_digest,
    build_promotion_audit,
    capture_memory,
    list_captures,
    promote_capture,
    slugify,
    transition_capture_status,
    unique_page_id,
)
from ..code_ingest.ingest_service import ingest_service_code
from ..code_ingest.validate import IngestValidationError, validate_service_id, validate_source_dir
from ..context_graph import build_context_graph, explain_context, query_neighbors, query_paths, scope_context_graph
from ..distillation import distill_daily, get_daily_captures, promote_daily_note
from ..extraction import retry_deadletter
from ..frontmatter import update_frontmatter
from ..frontmatter_spec import get_frontmatter_spec
from ..heartbeat import emit_heartbeat_captures, heartbeat_review
from ..ledger import utc_now
from ..link_graph import build_link_graph, page_links
from ..lint import lint_contradiction_review, lint_lore, lint_stale_queue
from ..lint_config import LintConfig
from ..post_capture import run_post_capture_side_effects
from ..precedent_search import search_precedents
from ..procedure_candidate import find_repeated_captures, propose_procedure_candidate
from ..provenance import get_capture_provenance, get_page_provenance
from ..rag.hybrid_retrieval import hybrid_retrieve_expanded
from ..recall import count_pending_captures, recall_hint, weights_for_query
from ..repository import InvalidPageId, LoreRepository
from ..route_utils import index_vectors_for_page, recall_actor_scope, stamp_capture_actor, stamp_trace_actor
from ..schemas import (
    CaptureRequest,
    ContextExplainQuery,
    ContextGraphNeighborQuery,
    ContextGraphPathQuery,
    DailyDistillRequest,
    MetadataUpdate,
    PrecedentSearchRequest,
    TraceCreateRequest,
    TraceEntry,
    TraceListResponse,
)
from .context import McpContext
from .decisions import build_decision_markdown, build_procedure_markdown, export_procedure_skill
from .dispatch import JsonRpcError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..config import LoreConfig

WRITE_TOOL_NAMES = {
    "lore_capture",
    "lore_apply_patch",
    "lore_consolidation_rollback",
    "lore_consolidation_run",
    "lore_create_trace",
    "lore_create_decision",
    "lore_create_procedure",
    "lore_create_stub",
    "lore_ingest_service",
    "lore_update_metadata",
    "lore_ack_recall",
    "lore_transition_capture",
    "lore_distill_daily",
    "lore_promote_daily",
    "lore_propose_procedure_candidate",
    "lore_retry_extraction_deadletter",
    "lore_heartbeat_audit",
    "lore_promote_capture",
    "lore_reject_patch",
    "lore_upsert_page",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "lore_overview",
        "title": "Lore Overview",
        "description": (
            "Discoverability index: returns the Lore MCP tool taxonomy (grouped by capability) plus the "
            "canonical capture -> recall -> consolidate memory loop. Call this first to learn which tool to use."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "lore_list_pages",
        "title": "List Lore Pages",
        "description": "List pages in Lore, optionally filtered by kind, visibility, or query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Optional kind such as project, service, decision, or runbook.",
                },
                "visibility": {
                    "type": "string",
                    "description": "Optional visibility such as public, internal, or private.",
                },
                "query": {"type": "string", "description": "Optional title/tag/summary filter."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            },
        },
    },
    {
        "name": "lore_read_page",
        "title": "Read Lore Page",
        "description": "Read a Markdown page from Lore.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Slash-separated page ID without .md, for example projects/example-project.",
                }
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "lore_search",
        "title": "Search Lore",
        "description": "Search Lore pages. Returns ranked results with snippets when FTS is available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms."},
                "kind": {"type": "string", "description": "Filter by page kind (project, service, runbook, etc.)."},
                "visibility": {"type": "string", "description": "Filter by visibility (internal, public)."},
                "status": {"type": "string", "description": "Filter by page status."},
                "namespace": {
                    "type": "string",
                    "description": "Filter by page namespace, such as projects or services.",
                },
                "lane": {
                    "type": "string",
                    "enum": ["project", "procedural", "ops", "companion", "draft"],
                    "description": "Filter by retrieval lane.",
                },
                "actor": {"type": "string", "description": "Filter by producing agent name."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "lore_list_lanes",
        "title": "List Lore Retrieval Lanes",
        "description": "List available retrieval lanes and their page counts. Lanes categorize memory by purpose: project, procedural, ops, companion, draft.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_list_actors",
        "title": "List Lore Actors",
        "description": "List known agents (actors) that have produced captures or pages in Lore, with their counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string", "description": "Admin-only actor filter with cross_actor."},
                "cross_actor": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "lore_rag_context",
        "title": "Lore RAG Context",
        "description": "Retrieve relevant Lore context for a query using hybrid search (BM25 + vector + graph).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query."},
                "limit": {"type": "integer", "description": "Max results (default 5).", "default": 5},
                "actor": {"type": "string", "description": "Admin-only claim actor filter with cross_actor."},
                "cross_actor": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "lore_rag_context_expanded",
        "title": "Lore RAG Context Expanded",
        "description": "Retrieve Lore context with multi-hop graph expansion, returning relevance paths, supporting/contradicting claims, related decisions, and relevant-because summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query."},
                "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 50},
                "expand_hops": {"type": "integer", "default": 2, "minimum": 0, "maximum": 4},
                "expand_edge_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Edge types to follow during expansion. Empty = all.",
                },
                "include_claims": {"type": "boolean", "default": True},
                "include_traces": {"type": "boolean", "default": False},
                "include_decisions": {"type": "boolean", "default": True},
                "actor": {"type": "string", "description": "Admin-only claim actor filter with cross_actor."},
                "cross_actor": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "lore_recall",
        "title": "Recall Lore Memory",
        "description": "Recency- and salience-weighted recall over the durable claim ledger. Ranks active memory by strength (reinforce/decay), recency, recall frequency, and -- when a query is given -- lexical relevance. Returns each claim's score breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional query to bias ranking by lexical relevance."},
                "subject": {"type": "string", "description": "Filter to claims about an exact normalized subject."},
                "lane": {
                    "type": "string",
                    "enum": ["project", "procedural", "ops", "companion", "draft"],
                    "description": "Filter to a retrieval lane.",
                },
                "actor": {"type": "string", "description": "Filter to claims produced by a specific agent."},
                "min_strength": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.0,
                    "description": "Minimum ledger strength to consider.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Number of top-ranked claims to skip for pagination.",
                },
                "record_access": {
                    "type": "boolean",
                    "default": False,
                    "description": "Explicitly stamp access on returned claims. Defaults false so reads are idempotent.",
                },
                "cross_actor": {
                    "type": "boolean",
                    "default": False,
                    "description": "Admin-only: explicitly allow recall outside the authenticated actor scope.",
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "lore_ack_recall",
        "title": "Acknowledge Lore Recall",
        "description": "Explicitly acknowledge that recalled claims were used, incrementing access_count and last_accessed_at for salience without affecting recency or decay age.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Claim candidate IDs returned by lore_recall.",
                },
                "actor": {"type": "string", "description": "Admin-only actor scope with cross_actor."},
                "cross_actor": {"type": "boolean", "default": False},
            },
            "required": ["candidate_ids"],
        },
    },
    {
        "name": "lore_link_graph",
        "title": "Lore Link Graph",
        "description": "Return Lore page links and broken internal links for graph-aware agent navigation.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_context_graph",
        "title": "Lore Context Graph",
        "description": "Get the full context graph spanning pages, captures, entities, claims, plans, traces, actors, tasks, policies, and sources with typed edges.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_graph_analytics",
        "title": "Lore Graph Analytics",
        "description": "Compute degree centrality, betweenness, community detection, and semantic entry points for the context graph. Results are advisory and cacheable.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_context_graph_neighbors",
        "title": "Lore Context Graph Neighbors",
        "description": "Find neighbors of a node in the Lore context graph, optionally filtered by direction, edge type, and node type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID to find neighbors for."},
                "direction": {"type": "string", "enum": ["outgoing", "incoming", "both"], "default": "both"},
                "edge_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to these edge types.",
                },
                "node_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter neighbors to these node types.",
                },
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lore_context_graph_paths",
        "title": "Lore Context Graph Paths",
        "description": "Find bounded paths between two nodes in the Lore context graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "Start node ID."},
                "target_id": {"type": "string", "description": "Target node ID."},
                "max_depth": {"type": "integer", "default": 3, "minimum": 1, "maximum": 6},
                "edge_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter edges to these types.",
                },
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "required": ["source_id", "target_id"],
        },
    },
    {
        "name": "lore_explain_context",
        "title": "Explain Lore Context",
        "description": "Explain the context around a node by expanding its neighborhood in the context graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node to explain."},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 3},
                "edge_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter edges to these types.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lore_page_links",
        "title": "Lore Page Links",
        "description": "Return outgoing links, backlinks, and missing links for one Lore page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Slash-separated page ID without .md, for example services/lore.",
                }
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "lore_lint",
        "title": "Lint Lore",
        "description": "Return knowledge-quality issues such as broken links, missing provenance, stale pages, and orphans.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_stale_pages",
        "title": "Lore Stale Pages",
        "description": "Return Lore pages that are stale or missing freshness review metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_contradiction_review",
        "title": "Lore Contradiction Review",
        "description": "Return contradiction and verification markers with surrounding context.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_frontmatter_spec",
        "title": "Lore Frontmatter Spec",
        "description": "Return the per-kind frontmatter contract for Lore pages.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_list_procedures",
        "title": "List Lore Procedures",
        "description": "List reusable agent workflow procedure pages with trigger and steps metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_create_procedure",
        "title": "Create Lore Procedure",
        "description": "Create a procedure page from structured workflow fields.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Procedure title."},
                "summary": {"type": "string", "description": "One-line summary of when to use the procedure."},
                "trigger": {"type": "string", "description": "Event or situation that starts the procedure."},
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered procedure steps.",
                },
                "preconditions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Required state before starting.",
                },
                "postconditions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Expected state after completion.",
                },
                "error_handling": {"type": "string", "description": "What to do if things go wrong."},
                "author": {"type": "string", "description": "Procedure author."},
                "schema_version": {"type": "string", "description": "Procedure schema version."},
            },
            "required": ["title", "summary", "trigger", "steps"],
        },
    },
    {
        "name": "lore_export_procedure",
        "title": "Export Lore Procedure",
        "description": "Export a procedure page as a skill-style Markdown document.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Procedure page ID to export."},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "lore_capture",
        "title": "Capture Lore Memory",
        "description": "Capture rough agent memory into a draft inbox or notes Markdown page for autonomous consolidation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "observation": {"type": "string", "description": "Raw observation or memory to capture."},
                "title": {"type": "string", "description": "Optional short capture title."},
                "namespace": {
                    "type": "string",
                    "enum": ["inbox", "notes"],
                    "default": "inbox",
                    "description": "Use inbox for shared intake or notes for agent-scoped notes.",
                },
                "agent": {"type": "string", "description": "Agent name for notes namespace."},
                "capture_date": {"type": "string", "description": "Optional ISO date for the capture path."},
                "source_task": {"type": "string", "description": "Optional Flow task or source task identifier."},
                "task_id": {"type": "string", "description": "Source task ID (e.g. flow_000123)."},
                "decision_id": {"type": "string", "description": "Linked decision page ID."},
                "trace_id": {"type": "string", "description": "Reasoning trace correlation ID."},
                "tool_calls": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Tool call records from the capturing session.",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Constraints that applied during capture.",
                },
                "policies_applied": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Policy IDs that were enforced.",
                },
                "related_pages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Related Lore page IDs.",
                },
                "confidence": {"type": "string", "description": "Confidence such as low, medium, high, or unknown."},
                "epistemic_status": {
                    "type": "string",
                    "enum": ["operator_declared", "retrieved", "inferred", "assumption"],
                    "description": "How was this knowledge obtained? operator_declared=human stated, retrieved=fetched from source, inferred=deduced, assumption=LLM guess.",
                },
                "suggested_target_page": {
                    "type": "string",
                    "description": "Canonical Lore page where this may eventually be promoted.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Evidence, source paths, or URLs behind the observation.",
                },
                "source_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Repo paths or file references supporting the observation.",
                },
                "source_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "HTTP/HTTPS URLs supporting the observation.",
                },
                "provenance": {"type": "object", "description": "Unified provenance references."},
                "evidence": {
                    "type": "string",
                    "description": "Supporting evidence text behind the observation.",
                },
                "lane": {
                    "type": "string",
                    "enum": ["project", "procedural", "ops", "companion", "draft"],
                    "description": "Retrieval lane for categorizing the capture.",
                },
                "actor": {
                    "type": "string",
                    "description": "Agent name that produced this capture.",
                },
            },
            "required": ["observation"],
        },
    },
    {
        "name": "lore_list_captures",
        "title": "List Lore Captures",
        "description": "List draft or reviewed capture pages waiting for promotion into canonical Lore pages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "default": "draft",
                    "description": "Capture status to list, or all for every capture status.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    },
    {
        "name": "lore_capture_digest",
        "title": "Lore Capture Digest",
        "description": "Summarize unreviewed captures grouped by date, source task, and suggested target page.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_transition_capture",
        "title": "Transition Capture Status",
        "description": "Change the status of a Lore capture page to draft, review, accepted, rejected, or archived.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Capture page ID, for example inbox/2026-05-01/my-capture.",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "review", "accepted", "rejected", "archived"],
                    "description": "New capture status.",
                },
            },
            "required": ["page_id", "status"],
        },
    },
    {
        "name": "lore_promote_capture",
        "title": "Promote Capture",
        "description": "Promote a reviewed capture into a canonical Lore page. Sets capture status to accepted and records the target page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Capture page ID to promote.",
                },
                "target_page_id": {
                    "type": "string",
                    "description": "Optional target canonical page ID. Falls back to suggested_target_page from capture frontmatter.",
                },
                "content": {
                    "type": "string",
                    "description": "Optional content for the target page. Required when overwriting existing pages. If omitted for new pages, uses capture body.",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "lore_promotion_audit",
        "title": "Lore Promotion Audit",
        "description": "Return the promotion audit trail: which captures were promoted to which pages, and which pages cite capture sources.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_create_stub",
        "title": "Create Lore Stub",
        "description": "Create a draft stub page for a missing internal Lore link.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Missing page ID to create, for example services/missing.",
                },
                "title": {"type": "string", "description": "Optional stub title."},
                "kind": {"type": "string", "default": "page", "description": "Page kind for the stub."},
                "source_page": {"type": "string", "description": "Page that linked to this missing target."},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "lore_update_metadata",
        "title": "Update Lore Metadata",
        "description": "Update owner, freshness, confidence, or status frontmatter without replacing the page body.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Page ID to update."},
                "owner": {"type": "string", "description": "Page owner."},
                "reviewed_at": {"type": "string", "description": "ISO date when the page was reviewed."},
                "stale_after": {"type": "string", "description": "ISO date after which the page needs review."},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"], "description": "Confidence level."},
                "epistemic_status": {
                    "type": "string",
                    "enum": ["operator_declared", "retrieved", "inferred", "assumption"],
                    "description": "How this page knowledge was obtained.",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "review", "accepted", "deprecated"],
                    "description": "Canonical metadata status.",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "lore_ingest_service",
        "title": "Ingest Service Code",
        "description": "Scan a service source directory and return route, symbol, and source file inventory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "string",
                    "description": "Lore service page ID, for example services/lore.",
                },
                "source_dir": {
                    "type": "string",
                    "description": "Local source directory to scan.",
                },
            },
            "required": ["service_id", "source_dir"],
        },
    },
    {
        "name": "lore_create_decision",
        "title": "Create Lore Decision",
        "description": "Create a decision record page from ADR-style fields.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Decision title."},
                "summary": {"type": "string", "description": "One-line summary."},
                "context": {"type": "string", "description": "Why this decision is needed."},
                "decision": {"type": "string", "description": "What was decided."},
                "consequences": {"type": "string", "description": "What changes as a result."},
                "deciders": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Decision makers.",
                },
                "status": {"type": "string", "default": "proposed", "description": "Decision status."},
            },
            "required": ["title", "summary", "context", "decision", "consequences"],
        },
    },
    {
        "name": "lore_create_trace",
        "description": "Record a reasoning trace - a concise rationale summary explaining why a decision was made. NOT for raw model chain-of-thought. Use this when making important agent decisions, choosing between alternatives, or applying policies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string", "description": "Agent or person making the decision."},
                "reason_summary": {
                    "type": "string",
                    "description": "Concise human-readable rationale (max 5000 chars).",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "abandoned"],
                    "description": "Trace status. Default: active.",
                },
                "parent_trace_id": {"type": "string", "description": "Parent trace ID for sub-decisions."},
                "context_refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["page", "capture", "task", "candidate"]},
                            "id": {"type": "string"},
                        },
                        "required": ["type", "id"],
                    },
                    "description": "Context entities examined during the decision.",
                },
                "tool_refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "action": {"type": "string"},
                            "result_summary": {"type": "string"},
                        },
                        "required": ["tool"],
                    },
                    "description": "Tools called during the decision.",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Constraints that applied.",
                },
                "policy_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Policy IDs that governed the decision.",
                },
                "alternatives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "rejected_reason": {"type": "string"},
                        },
                        "required": ["description"],
                    },
                    "description": "Alternatives considered and why they were rejected.",
                },
                "outcome": {
                    "type": "string",
                    "description": "Outcome of the decision (fill in later if unknown at creation time).",
                },
                "related_ids": {
                    "type": "object",
                    "description": "Linked entities: task_id, capture_id, page_id, candidate_id, decision_id.",
                    "properties": {
                        "task_id": {"type": "string"},
                        "capture_id": {"type": "string"},
                        "page_id": {"type": "string"},
                        "candidate_id": {"type": "string"},
                        "decision_id": {"type": "string"},
                    },
                },
                "provenance": {"type": "object", "description": "Unified provenance references."},
            },
            "required": ["actor", "reason_summary"],
        },
    },
    {
        "name": "lore_get_trace",
        "description": "Retrieve a reasoning trace by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "description": "The trace ID to retrieve."},
                "cross_actor": {
                    "type": "boolean",
                    "description": "Admin-only: explicitly allow access outside the authenticated actor scope.",
                },
            },
            "required": ["trace_id"],
        },
    },
    {
        "name": "lore_get_provenance",
        "description": "Return provenance references for a capture, trace, or page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "enum": ["capture", "trace", "page"]},
                "entity_id": {"type": "string"},
                "cross_actor": {
                    "type": "boolean",
                    "description": "Admin-only: explicitly allow access outside the authenticated actor scope.",
                },
            },
            "required": ["entity_type", "entity_id"],
        },
    },
    {
        "name": "lore_list_traces",
        "title": "List Lore Traces",
        "description": "Query reasoning traces by actor, status, task, or other filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string", "description": "Filter by actor name."},
                "status": {"type": "string", "description": "Filter by status (active, completed, abandoned)."},
                "task_id": {"type": "string", "description": "Filter by linked task ID."},
                "cross_actor": {
                    "type": "boolean",
                    "description": "Admin-only: explicitly allow recall outside the authenticated actor scope.",
                },
                "limit": {"type": "integer", "description": "Max results. Default: 20."},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Number of traces to skip for pagination.",
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "lore_list_policies",
        "title": "List Lore Policies",
        "description": "List policy rules that gate patch planning decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "gate": {
                    "type": "string",
                    "description": "Optional gate filter, such as auto-apply or protected-surface.",
                },
                "enabled_only": {"type": "boolean", "default": True, "description": "Only return enabled policies."},
            },
        },
    },
    {
        "name": "lore_find_precedents",
        "title": "Find Lore Precedents",
        "description": "Search for prior traces, decisions, policies, and pages that match entity, situation, actor, policy, keyword, or task criteria.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name filter."},
                "situation_type": {"type": "string", "description": "Situation type filter."},
                "lane": {
                    "type": "string",
                    "enum": ["project", "procedural", "ops", "companion", "draft"],
                    "description": "Retrieval lane filter.",
                },
                "actor": {"type": "string", "description": "Agent name filter."},
                "policy": {"type": "string", "description": "Policy ID filter."},
                "keyword": {"type": "string", "description": "Keyword filter."},
                "task_ref": {"type": "string", "description": "Task ID reference."},
                "cross_actor": {
                    "type": "boolean",
                    "description": "Admin-only: explicitly allow recall outside the authenticated actor scope.",
                },
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "lore_get_policy",
        "title": "Get Lore Policy",
        "description": "Retrieve one patch planning policy by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "policy_id": {"type": "string", "description": "Policy ID, for example auto-apply:v1."},
            },
            "required": ["policy_id"],
        },
    },
    {
        "name": "lore_upsert_page",
        "title": "Upsert Lore Page",
        "description": "Create or replace a Markdown page in Lore.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Slash-separated page ID without .md, for example services/lore.",
                },
                "content": {
                    "type": "string",
                    "description": "Complete Markdown document, including optional frontmatter.",
                },
            },
            "required": ["page_id", "content"],
        },
    },
    {
        "name": "lore_distill_daily",
        "title": "Distill Daily Note",
        "description": "Distill session captures for a given date into a daily note page under dailies/YYYY-MM-DD. Defaults to today.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Defaults to today.",
                },
                "actor": {
                    "type": "string",
                    "description": "Agent performing the distillation.",
                },
            },
        },
    },
    {
        "name": "lore_get_daily",
        "title": "Get Daily Captures",
        "description": "List session captures for a specific date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD).",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "lore_promote_daily",
        "title": "Promote Daily Note",
        "description": "Confirm a daily note has been reviewed and mark it as the canonical daily page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD).",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "lore_heartbeat_review",
        "title": "Lore Heartbeat Review",
        "description": "Return an aggregated freshness and quality report covering stale pages, contradictions, low-confidence pages, expired facts, and procedure issues.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_heartbeat_summary",
        "title": "Lore Heartbeat Summary",
        "description": "Return just the issue counts per category without item details.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_heartbeat_audit",
        "title": "Lore Heartbeat Self-Audit Captures",
        "description": "Run heartbeat review and create capture drafts for any issues found (stale, contradictory, low-confidence, expired, or procedure problems). Idempotent: skips categories already captured today.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_find_repeated_captures",
        "title": "Find Repeated Captures",
        "description": "Find groups of captures with similar content that may indicate a repeated procedure worth codifying.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lore_propose_procedure_candidate",
        "title": "Propose Procedure Candidate",
        "description": "Create a procedure candidate page from repeated captures, linking back to source captures.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capture_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capture page IDs to group into a procedure candidate (minimum 2).",
                },
                "title": {"type": "string", "description": "Optional override title for the candidate."},
                "trigger": {"type": "string", "description": "Optional override trigger description."},
                "lane": {"type": "string", "description": "Optional retrieval lane."},
            },
            "required": ["capture_ids"],
        },
    },
    {
        "name": "lore_retry_extraction_deadletter",
        "title": "Retry Extraction Dead-Letter",
        "description": "Reset and retry one extraction dead-letter by ID, resolving it when candidates are produced.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deadletter_id": {"type": "string", "description": "Extraction dead-letter ID to retry."},
            },
            "required": ["deadletter_id"],
        },
    },
    {
        "name": "lore_consolidation_status",
        "title": "Lore Consolidation Status",
        "description": "Get consolidation pipeline status: last run, plan counts by status, stuck runs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "lore_consolidation_run",
        "title": "Run Lore Consolidation",
        "description": "Run the consolidation pipeline: extract candidates, generate patch plans, and optionally auto-apply safe plans.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "default": True},
                "batch_size": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "max_auto_apply": {"type": "integer", "default": 0, "minimum": 0, "maximum": 100},
                "force_reextract": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "lore_consolidation_rollback",
        "title": "Rollback Lore Consolidation Patch",
        "description": "Roll back an applied patch plan, restoring the page to its pre-patch state.",
        "inputSchema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string", "description": "Patch plan ID to roll back."}},
            "required": ["plan_id"],
        },
    },
    {
        "name": "lore_list_patch_plans",
        "title": "List Lore Patch Plans",
        "description": "List patch plans, optionally filtered by status or target page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "applied", "rejected", "rolled_back"]},
                "target_page_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
        },
    },
    {
        "name": "lore_preview_patch",
        "title": "Preview Lore Patch",
        "description": "Preview the diff for a patch plan before applying it.",
        "inputSchema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string", "description": "Patch plan ID to preview."}},
            "required": ["plan_id"],
        },
    },
    {
        "name": "lore_apply_patch",
        "title": "Apply Lore Patch",
        "description": "Apply a patch plan to its target canonical page. Use force=true for plans that are not auto-appliable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Patch plan ID to apply."},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "lore_reject_patch",
        "title": "Reject Lore Patch",
        "description": "Reject a pending patch plan with an optional reason.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Patch plan ID to reject."},
                "reason": {"type": "string", "description": "Optional rejection reason."},
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "lore_review_batch",
        "title": "Review Lore Consolidation Batch",
        "description": "Summarize a consolidation batch for agent review, grouped by risk level with recommendations.",
        "inputSchema": {
            "type": "object",
            "properties": {"batch_id": {"type": "string", "description": "Optional consolidation batch ID."}},
        },
    },
]


def _handle_lore_list_pages(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    limit = int(arguments.get("limit") or 50)
    pages = ctx.repo.list_pages(
        kind=optional_string(arguments.get("kind")),
        visibility=optional_string(arguments.get("visibility")),
        q=optional_string(arguments.get("query")),
        limit=max(1, min(limit, 100)),
    )
    payload = {"pages": [page.model_dump() for page in pages]}
    return tool_result(payload, summarize_pages(pages))


def _handle_lore_read_page(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    page = ctx.repo.read_page(page_id)
    if page is None:
        return tool_result({"page_id": page_id}, f"Lore page not found: {page_id}", is_error=True)
    return tool_result({"page": page.model_dump()}, page.content)


def _handle_lore_search(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    query = require_string(arguments.get("query"), "query")
    limit = int(arguments.get("limit") or 20)
    limit_val = max(1, min(limit, 50))
    kind = optional_string(arguments.get("kind"))
    visibility = optional_string(arguments.get("visibility"))
    status = optional_string(arguments.get("status"))
    namespace = optional_string(arguments.get("namespace"))
    lane = optional_string(arguments.get("lane"))
    actor = optional_string(arguments.get("actor"))

    if ctx.search_index is not None:
        fts_hits = ctx.search_index.search(query, kind=kind, lane=lane, actor=actor, limit=50)
        if fts_hits or ctx.search_index.has_pages():
            hits = filter_fts_hits(fts_hits, visibility=visibility, status=status, namespace=namespace, lane=lane)[
                :limit_val
            ]
            payload = {"query": query, "hits": hits}
            return tool_result(payload, summarize_fts_search(payload))

    results = ctx.repo.search(query, kind=kind, visibility=visibility, limit=limit_val)
    payload = results.model_dump()
    payload["hits"] = filter_repo_hits(payload["hits"], status=status, namespace=namespace)
    return tool_result(payload, summarize_search(payload))


def _handle_lore_list_lanes(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    if ctx.search_index is None:
        return tool_result({"lanes": []}, "Search index is not available.")
    lanes = ctx.search_index.list_lanes()
    return tool_result({"lanes": lanes}, f"Found {len(lanes)} retrieval lane(s).")


def _mcp_actor_scope(ctx: McpContext, arguments: dict[str, Any]) -> str | None:
    requested_actor = optional_string(arguments.get("actor"))
    cross_actor = bool(arguments.get("cross_actor", False))
    if ctx.request is None:
        return requested_actor
    try:
        return recall_actor_scope(ctx.request, requested_actor=requested_actor, cross_actor=cross_actor)
    except PermissionError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc


def _handle_lore_list_actors(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    if ctx.search_index is None:
        return tool_result({"actors": []}, "Search index is not available.")
    actors = ctx.search_index.list_actors(actor=_mcp_actor_scope(ctx, arguments))
    return tool_result({"actors": actors}, f"Found {len(actors)} known actor(s).")


def _handle_lore_rag_context(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    query = require_string(arguments.get("query"), "query")
    limit = max(1, min(int(arguments.get("limit") or 5), 50))
    expand_hops = max(0, min(int(arguments.get("expand_hops", 2)), 4))
    expand_edge_types = arguments.get("expand_edge_types") or None
    if expand_edge_types is not None and not isinstance(expand_edge_types, list):
        raise JsonRpcError(-32602, "expand_edge_types must be an array.")
    include_claims = bool(arguments.get("include_claims", True))
    include_traces = bool(arguments.get("include_traces", False))
    include_decisions = bool(arguments.get("include_decisions", True))
    claim_actor = _mcp_actor_scope(ctx, arguments)
    if ctx.vector_store is None:
        return tool_result({"query": query, "results": []}, "RAG vector store is not configured.", is_error=True)
    ledger = require_service(ctx.ledger_db, "ledger database")
    graph = build_context_graph(ctx.repo, ledger)
    payload = enrich_expanded_results(
        ctx.repo,
        hybrid_retrieve_expanded(
            query,
            ctx.search_index,
            ctx.vector_store,
            graph,
            ledger,
            limit=limit,
            expand_hops=expand_hops,
            expand_edge_types=[str(edge_type) for edge_type in expand_edge_types] if expand_edge_types else None,
            include_claims=include_claims,
            include_traces=include_traces,
            include_decisions=include_decisions,
            claim_actor=claim_actor,
        ),
    )
    return tool_result(payload, summarize_rag_context(payload))


def _handle_lore_rag_context_expanded(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    query = require_string(arguments.get("query"), "query")
    limit = max(1, min(int(arguments.get("limit") or 5), 50))
    expand_hops = max(0, min(int(arguments.get("expand_hops", 2)), 4))
    expand_edge_types = arguments.get("expand_edge_types") or None
    if expand_edge_types is not None and not isinstance(expand_edge_types, list):
        raise JsonRpcError(-32602, "expand_edge_types must be an array.")
    include_claims = bool(arguments.get("include_claims", True))
    include_traces = bool(arguments.get("include_traces", False))
    include_decisions = bool(arguments.get("include_decisions", True))
    claim_actor = _mcp_actor_scope(ctx, arguments)
    if ctx.vector_store is None:
        return tool_result({"query": query, "results": []}, "RAG vector store is not configured.", is_error=True)
    ledger = require_service(ctx.ledger_db, "ledger database")
    graph = build_context_graph(ctx.repo, ledger)
    payload = enrich_rag_results(
        ctx.repo,
        hybrid_retrieve_expanded(
            query,
            ctx.search_index,
            ctx.vector_store,
            graph,
            ledger,
            limit=limit,
            expand_hops=expand_hops,
            expand_edge_types=[str(edge_type) for edge_type in expand_edge_types] if expand_edge_types else None,
            include_claims=include_claims,
            include_traces=include_traces,
            include_decisions=include_decisions,
            claim_actor=claim_actor,
        ),
    )
    return tool_result(payload, summarize_rag_context(payload))


def _handle_lore_recall(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    query = optional_string(arguments.get("query"))
    subject = optional_string(arguments.get("subject"))
    lane = optional_string(arguments.get("lane"))
    actor = optional_string(arguments.get("actor"))
    min_strength = max(0.0, min(float(arguments.get("min_strength") or 0.0), 1.0))
    limit = max(1, min(int(arguments.get("limit") or 20), 200))
    offset = max(0, int(arguments.get("offset") or 0))
    record_access = bool(arguments.get("record_access", False))
    cross_actor = bool(arguments.get("cross_actor", False))
    if ctx.request is not None:
        try:
            actor = recall_actor_scope(ctx.request, requested_actor=actor, cross_actor=cross_actor)
        except PermissionError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
    # Always over-fetch one extra row to detect has_more -- including when
    # record_access is set. Fetch WITHOUT stamping so the probe row is never
    # marked, then stamp the actually-returned page explicitly below. (Disabling
    # the probe for record_access left has_more permanently False on that path.)
    rows = ledger.recall_claims(
        query=query,
        subject=subject,
        lane=lane,
        actor=actor,
        min_strength=min_strength,
        limit=limit + 1,
        offset=offset,
        record_access=False,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    if record_access and rows:
        ledger.record_claim_access([str(row["candidate_id"]) for row in rows], actor=actor)
    claims = [_recall_claim_payload(row) for row in rows]
    # Self-diagnosing recall, matching the REST surface: a count=0 result carries the
    # pending-consolidation count and a hint so an MCP agent never hits a silent dead end.
    pending_captures = count_pending_captures(ctx.repo, ledger)
    hint = recall_hint(len(claims), pending_captures)
    payload = {
        "query": query,
        "count": len(claims),
        "offset": offset,
        "has_more": has_more,
        "weights": weights_for_query(
            query,
            semantic_available=bool(query and query.strip()) and ledger.semantic_recall_enabled,
        ),
        "claims": claims,
        "pending_captures": pending_captures,
        "hint": hint,
    }
    return tool_result(payload, summarize_recall(payload))


def _handle_lore_ack_recall(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    raw_ids = arguments.get("candidate_ids")
    if not isinstance(raw_ids, list):
        raise JsonRpcError(-32602, "candidate_ids must be an array.")
    candidate_ids = [str(candidate_id) for candidate_id in raw_ids if candidate_id]
    ledger = require_service(ctx.ledger_db, "ledger database")
    actor = _mcp_actor_scope(ctx, arguments)
    timestamp = utc_now()
    acknowledged_count = ledger.record_claim_access(candidate_ids, now=timestamp, actor=actor)
    payload = {"acknowledged_count": acknowledged_count, "timestamp": timestamp}
    return tool_result(payload, f"Acknowledged {acknowledged_count} recalled claim(s).")


def _handle_lore_overview(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    payload = {
        "core_loop": ["lore_capture", "lore_consolidation_run", "lore_recall", "lore_ack_recall"],
        "core_loop_description": "capture -> consolidate -> recall -> acknowledge",
        "taxonomy": {
            "discovery": [
                "lore_overview",
                "lore_list_pages",
                "lore_list_lanes",
                "lore_list_actors",
                "lore_frontmatter_spec",
            ],
            "read_pages": ["lore_read_page", "lore_search", "lore_page_links", "lore_link_graph"],
            "memory": ["lore_capture", "lore_recall", "lore_ack_recall", "lore_list_captures", "lore_capture_digest"],
            "consolidation": ["lore_consolidation_status", "lore_consolidation_run", "lore_consolidation_rollback"],
            "graph": [
                "lore_context_graph",
                "lore_graph_analytics",
                "lore_context_graph_neighbors",
                "lore_context_graph_paths",
                "lore_explain_context",
            ],
            "traces": ["lore_create_trace", "lore_get_trace", "lore_list_traces"],
            "write_pages": sorted(WRITE_TOOL_NAMES),
        },
        "tool_count": len(TOOLS),
    }
    return tool_result(payload, "Lore tool taxonomy + capture->recall->consolidate loop.")


def _handle_lore_link_graph(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    graph = build_link_graph(ctx.repo)
    payload = graph.model_dump()
    return tool_result(payload, summarize_link_graph(payload))


def _handle_lore_context_graph(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    graph = scope_context_graph(build_context_graph(ctx.repo, ledger), _mcp_actor_scope(ctx, arguments))
    payload = graph.model_dump(mode="json")
    return tool_result(payload, f"Context graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")


def _handle_lore_graph_analytics(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    graph = scope_context_graph(build_context_graph(ctx.repo, ledger), _mcp_actor_scope(ctx, arguments))
    result = GraphAnalytics(graph).compute()
    payload = result.model_dump(mode="json")
    return tool_result(payload, f"Graph analytics: {result.node_count} nodes, {len(result.communities)} communities")


def _handle_lore_context_graph_neighbors(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    node_id = require_string(arguments.get("node_id"), "node_id")
    direction = arguments.get("direction", "both")
    edge_types = arguments.get("edge_types", [])
    node_types = arguments.get("node_types", [])
    limit = max(1, min(int(arguments.get("limit", 50)), 500))
    ledger = require_service(ctx.ledger_db, "ledger database")
    graph = scope_context_graph(build_context_graph(ctx.repo, ledger), _mcp_actor_scope(ctx, arguments))
    query = ContextGraphNeighborQuery(
        node_id=node_id,
        direction=direction,
        edge_types=edge_types,
        node_types=node_types,
        limit=limit,
    )
    result = query_neighbors(graph, query)
    return tool_result(result.model_dump(mode="json"), f"Found {result.total} neighbors for {node_id}")


def _handle_lore_context_graph_paths(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    source_id = require_string(arguments.get("source_id"), "source_id")
    target_id = require_string(arguments.get("target_id"), "target_id")
    max_depth = max(1, min(int(arguments.get("max_depth", 3)), 6))
    edge_types = arguments.get("edge_types", [])
    limit = max(1, min(int(arguments.get("limit", 10)), 50))
    ledger = require_service(ctx.ledger_db, "ledger database")
    graph = scope_context_graph(build_context_graph(ctx.repo, ledger), _mcp_actor_scope(ctx, arguments))
    query = ContextGraphPathQuery(
        source_id=source_id,
        target_id=target_id,
        max_depth=max_depth,
        edge_types=edge_types,
        limit=limit,
    )
    result = query_paths(graph, query)
    return tool_result(
        result.model_dump(mode="json"), f"Found {len(result.paths)} paths from {source_id} to {target_id}"
    )


def _handle_lore_explain_context(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    node_id = require_string(arguments.get("node_id"), "node_id")
    depth = max(1, min(int(arguments.get("depth", 2)), 3))
    edge_types = arguments.get("edge_types", [])
    ledger = require_service(ctx.ledger_db, "ledger database")
    graph = scope_context_graph(build_context_graph(ctx.repo, ledger), _mcp_actor_scope(ctx, arguments))
    query = ContextExplainQuery(node_id=node_id, depth=depth, edge_types=edge_types)
    result = explain_context(graph, query)
    return tool_result(result.model_dump(mode="json"), result.explanation)


def _handle_lore_page_links(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    links = page_links(ctx.repo, page_id)
    if links is None:
        return tool_result({"page_id": page_id}, f"Lore page not found: {page_id}", is_error=True)
    payload = links.model_dump()
    return tool_result(payload, summarize_page_links(payload))


def _handle_lore_lint(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    lint = lint_lore(ctx.repo)
    payload = lint.model_dump()
    return tool_result(payload, summarize_lint(payload))


def _handle_lore_stale_pages(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    stale = lint_stale_queue(ctx.repo)
    payload = stale.model_dump()
    return tool_result(payload, summarize_stale_pages(payload))


def _handle_lore_contradiction_review(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    review = lint_contradiction_review(ctx.repo)
    payload = review.model_dump()
    return tool_result(payload, summarize_contradiction_review(payload))


def _handle_lore_frontmatter_spec(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    spec = get_frontmatter_spec()
    payload = spec.model_dump()
    return tool_result(payload, f"{len(payload['specs'])} frontmatter kind specs.")


def _handle_lore_list_procedures(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    pages = ctx.repo.list_pages(kind="procedure")
    procedures = []
    for summary in pages:
        page = ctx.repo.read_page(summary.id)
        if page is None:
            continue
        procedures.append(
            {
                "page": summary.model_dump(),
                "trigger": optional_string(page.frontmatter.get("trigger")),
                "steps": string_arguments(page.frontmatter.get("steps")),
                "preconditions": string_arguments(page.frontmatter.get("preconditions")),
                "postconditions": string_arguments(page.frontmatter.get("postconditions")),
                "error_handling": optional_string(page.frontmatter.get("error_handling")),
            }
        )
    payload = {"procedures": procedures}
    return tool_result(payload, summarize_procedures(payload))


def _handle_lore_create_procedure(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    title = require_string(arguments.get("title"), "title")
    summary = require_string(arguments.get("summary"), "summary")
    trigger = require_string(arguments.get("trigger"), "trigger")
    steps = string_arguments(arguments.get("steps"))
    if not steps:
        raise JsonRpcError(-32602, "Missing required field: steps")
    preconditions = string_arguments(arguments.get("preconditions"))
    postconditions = string_arguments(arguments.get("postconditions"))
    error_handling = optional_string(arguments.get("error_handling")) or ""
    author = optional_string(arguments.get("author")) or ""
    schema_version = optional_string(arguments.get("schema_version")) or "1.0"
    page_id = unique_page_id(ctx.repo, f"procedures/{slugify(title)}")
    content = build_procedure_markdown(
        title=title,
        summary=summary,
        trigger=trigger,
        steps=steps,
        preconditions=preconditions,
        postconditions=postconditions,
        error_handling=error_handling,
        author=author,
        schema_version=schema_version,
    )
    try:
        page = ctx.repo.upsert_page(page_id, content)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(page)
    index_vector_page(ctx.vector_store, page)
    invalidate_graph_cache(ctx.graph_cache)
    return tool_result({"page": page.model_dump()}, f"Created Lore procedure: {page.id}")


def _handle_lore_export_procedure(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    page = ctx.repo.read_page(page_id)
    if page is None or page.kind != "procedure":
        return tool_result({"page_id": page_id}, f"Lore procedure not found: {page_id}", is_error=True)
    content = export_procedure_skill(page)
    return tool_result({"page_id": page.id, "content": content}, content)


def _handle_lore_capture(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    try:
        capture_request = CaptureRequest(**arguments)
        if ctx.request is not None:
            capture_request = stamp_capture_actor(capture_request, ctx.request)
        page = capture_memory(ctx.repo, capture_request)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    except ValidationError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.request is not None:
        run_post_capture_side_effects(
            app=ctx.request.app,
            page=page,
            search_idx=ctx.search_index,
            vector_store=ctx.vector_store,
            graph_cache=ctx.graph_cache,
        )
    else:
        if ctx.search_index is not None:
            ctx.search_index.upsert_page_from_detail(page)
        index_vector_page(ctx.vector_store, page)
        invalidate_graph_cache(ctx.graph_cache)
    payload = {"page": page.model_dump()}
    return tool_result(payload, f"Captured Lore memory: {page.id}")


def _handle_lore_list_captures(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    limit = int(arguments.get("limit") or 50)
    captures = list_captures(
        ctx.repo,
        status=optional_string(arguments.get("status")) or "draft",
        limit=max(1, min(limit, 200)),
    )
    payload = captures.model_dump()
    return tool_result(payload, summarize_captures(payload))


def _handle_lore_capture_digest(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    digest = build_capture_digest(ctx.repo)
    payload = digest.model_dump()
    lines = [f"Draft: {digest.total_draft}, Review: {digest.total_review}"]
    if digest.by_date:
        lines.append("By date:")
        for group in digest.by_date:
            lines.append(f"  {group.key}: {group.count}")
    if digest.by_source_task:
        lines.append("By source task:")
        for group in digest.by_source_task:
            lines.append(f"  {group.key}: {group.count}")
    if digest.by_suggested_target:
        lines.append("By suggested target:")
        for group in digest.by_suggested_target:
            lines.append(f"  {group.key}: {group.count}")
    return tool_result(payload, "\n".join(lines))


def _handle_lore_transition_capture(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    new_status = require_string(arguments.get("status"), "status")
    valid = {"draft", "review", "accepted", "rejected", "archived"}
    if new_status not in valid:
        raise JsonRpcError(-32602, f"Invalid status. Must be one of: {', '.join(sorted(valid))}")
    try:
        page = transition_capture_status(ctx.repo, page_id, new_status)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    except ValueError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(page)
    index_vector_page(ctx.vector_store, page)
    invalidate_graph_cache(ctx.graph_cache)
    return tool_result({"page": page.model_dump()}, f"Transitioned capture {page.id} to {new_status}.")


def _handle_lore_promote_capture(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    target = optional_string(arguments.get("target_page_id"))
    content = optional_string(arguments.get("content"))
    try:
        page = promote_capture(ctx.repo, page_id, target_page_id=target, content=content)
    except CaptureNotFoundError as exc:
        return tool_result({"page_id": page_id}, str(exc), is_error=True)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    except ValueError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(page)
    index_vector_page(ctx.vector_store, page)
    invalidate_graph_cache(ctx.graph_cache)
    return tool_result({"page": page.model_dump()}, f"Promoted capture to {page.id}.")


def _handle_lore_promotion_audit(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    audit = build_promotion_audit(ctx.repo)
    payload = audit.model_dump()
    lines = []
    for rec in audit.promoted_captures:
        lines.append(f"{rec.capture_id} -> {rec.target_page_id} ({rec.capture_status})")
    for page in audit.pages_with_capture_sources:
        caps = ", ".join(capture.capture_id for capture in page.source_captures)
        lines.append(f"{page.page_id} sources: {caps}")
    return tool_result(payload, "\n".join(lines) or "No promotions recorded.")


def _handle_lore_create_stub(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    try:
        existing = ctx.repo.read_page(page_id)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if existing is not None:
        return tool_result({"page_id": page_id}, f"Lore page already exists: {page_id}", is_error=True)

    title = optional_string(arguments.get("title")) or page_id.rsplit("/", 1)[-1].replace("-", " ").title()
    kind = optional_string(arguments.get("kind")) or "page"
    source_page = optional_string(arguments.get("source_page")) or "stub-creation"
    stub_content = f"""---
title: {json.dumps(title)}
kind: {kind}
visibility: internal
summary: "Stub page created from broken link."
sources:
  - {source_page}
status: stub
---

# {title}

This page was auto-created as a stub. Replace with actual content.
"""
    try:
        page = ctx.repo.upsert_page(page_id, stub_content)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(page)
    index_vector_page(ctx.vector_store, page)
    invalidate_graph_cache(ctx.graph_cache)
    return tool_result({"page": page.model_dump()}, f"Created Lore stub: {page.id}")


def _handle_lore_update_metadata(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    try:
        page = ctx.repo.read_page(page_id)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if page is None:
        return tool_result({"page_id": page_id}, f"Lore page not found: {page_id}", is_error=True)
    try:
        payload = MetadataUpdate(**{key: value for key, value in arguments.items() if key != "page_id"})
    except ValidationError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    updates = payload.model_dump(exclude_none=True)
    updated_content = update_frontmatter(page.content, updates)
    try:
        updated_page = ctx.repo.upsert_page(page.id, updated_content)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(updated_page)
    index_vector_page(ctx.vector_store, updated_page)
    invalidate_graph_cache(ctx.graph_cache)
    return tool_result({"page": updated_page.model_dump()}, f"Updated Lore metadata: {updated_page.id}")


def _handle_lore_ingest_service(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    role = str(getattr(ctx.request.state, "lore_role", "") or "").strip() if ctx.request else ""
    if role not in ("admin", ""):
        raise JsonRpcError(-32602, "Admin access required for code ingest.")
    service_id = require_string(arguments.get("service_id"), "service_id")
    source_dir = require_string(arguments.get("source_dir"), "source_dir")
    # Validate service_id
    try:
        validate_service_id(service_id)
    except IngestValidationError as e:
        raise JsonRpcError(-32602, str(e)) from e
    # Validate source_dir against configured roots and limits. Fail closed when
    # config is unavailable: without it the allowed-roots/traversal/limit gate
    # (L-SEC-10) cannot run, so ingestion of an arbitrary path must be refused.
    cfg: LoreConfig | None = ctx.config
    if cfg is None:
        raise JsonRpcError(-32603, "Code ingest unavailable: server config not provided.")
    try:
        source_dir = str(validate_source_dir(source_dir, cfg))
    except IngestValidationError as e:
        raise JsonRpcError(-32602, str(e)) from e
    inventory = ingest_service_code(service_id, source_dir)
    payload = inventory.model_dump()
    ctx.code_inventories[service_id] = payload
    return tool_result(payload, summarize_inventory(payload))


def _handle_lore_create_decision(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    title = require_string(arguments.get("title"), "title")
    summary = require_string(arguments.get("summary"), "summary")
    context = require_string(arguments.get("context"), "context")
    decision = require_string(arguments.get("decision"), "decision")
    consequences = require_string(arguments.get("consequences"), "consequences")
    deciders = string_arguments(arguments.get("deciders"))
    status = optional_string(arguments.get("status")) or "proposed"
    page_id = unique_page_id(ctx.repo, f"decisions/{slugify(title)}")
    content = build_decision_markdown(
        title=title,
        summary=summary,
        context=context,
        decision=decision,
        consequences=consequences,
        deciders=deciders,
        status=status,
    )
    try:
        page = ctx.repo.upsert_page(page_id, content)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(page)
    index_vector_page(ctx.vector_store, page)
    invalidate_graph_cache(ctx.graph_cache)
    return tool_result({"page": page.model_dump()}, f"Created Lore decision: {page.id}")


def _handle_lore_create_trace(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    try:
        payload = TraceCreateRequest(**arguments)
    except ValidationError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.request is not None:
        payload = stamp_trace_actor(payload, ctx.request)
    trace = TraceEntry(trace_id="", **payload.model_dump())
    stored = ledger.store_trace(trace)
    content = stored.model_dump(mode="json")
    return tool_result(content, f"Created reasoning trace: {stored.trace_id}")


def _handle_lore_get_trace(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    trace_id = require_string(arguments.get("trace_id"), "trace_id")
    trace = ledger.get_trace(trace_id)
    if trace is None:
        return tool_result({"trace_id": trace_id}, f"Trace not found: {trace_id}", is_error=True)
    if ctx.request is not None:
        try:
            recall_actor_scope(
                ctx.request,
                requested_actor=trace.actor,
                cross_actor=bool(arguments.get("cross_actor", False)),
            )
        except PermissionError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
    return tool_result(trace.model_dump(mode="json"), f"Retrieved reasoning trace: {trace.trace_id}")


def _handle_lore_get_provenance(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    entity_type = require_string(arguments.get("entity_type"), "entity_type")
    entity_id = require_string(arguments.get("entity_id"), "entity_id")
    if entity_type not in {"capture", "trace", "page"}:
        raise JsonRpcError(-32602, "entity_type must be one of: capture, trace, page")
    if entity_type == "capture":
        provenance = get_capture_provenance(ctx.repo, entity_id)
        if provenance is None:
            return tool_result(
                {"entity_type": entity_type, "entity_id": entity_id}, f"Capture not found: {entity_id}", is_error=True
            )
    elif entity_type == "trace":
        ledger = require_service(ctx.ledger_db, "ledger database")
        trace = ledger.get_trace(entity_id)
        if trace is None:
            return tool_result(
                {"entity_type": entity_type, "entity_id": entity_id}, f"Trace not found: {entity_id}", is_error=True
            )
        if ctx.request is not None:
            try:
                recall_actor_scope(
                    ctx.request,
                    requested_actor=trace.actor,
                    cross_actor=bool(arguments.get("cross_actor", False)),
                )
            except PermissionError as exc:
                raise JsonRpcError(-32602, str(exc)) from exc
        provenance = trace.provenance
    else:
        provenance = get_page_provenance(ctx.repo, entity_id)
    payload = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "provenance": (provenance.model_dump(mode="json") if provenance is not None else {}),
    }
    return tool_result(payload, f"Retrieved provenance for {entity_type}: {entity_id}")


def _handle_lore_list_traces(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    limit = max(1, min(int(arguments.get("limit") or 20), 500))
    offset = max(0, int(arguments.get("offset") or 0))
    filters = {
        "actor": _mcp_actor_scope(ctx, arguments),
        "status": optional_string(arguments.get("status")),
        "task_id": optional_string(arguments.get("task_id")),
    }
    traces = ledger.list_traces(**filters, limit=limit, offset=offset)
    total = ledger.count_traces(**filters)
    response = TraceListResponse(traces=traces, total=total, limit=limit, offset=offset)
    payload = response.model_dump(mode="json")
    # TraceListResponse has no has_more field; expose it on the dict so an agent
    # can page without recomputing from total/offset.
    payload["has_more"] = offset + len(traces) < total
    return tool_result(payload, summarize_traces(payload))


def _handle_lore_list_policies(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    enabled_only = bool(arguments.get("enabled_only", True))
    policies = ledger.list_policies(
        gate=optional_string(arguments.get("gate")),
        enabled_only=enabled_only,
    )
    payload = {"count": len(policies), "policies": [policy.model_dump(mode="json") for policy in policies]}
    return tool_result(payload, f"Found {len(policies)} policy rule(s).")


def _handle_lore_find_precedents(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    limit = max(1, min(int(arguments.get("limit", 20)), 100))
    actor = _mcp_actor_scope(ctx, arguments)
    result = search_precedents(
        ctx.repo,
        ledger,
        PrecedentSearchRequest(
            entity=optional_string(arguments.get("entity")),
            situation_type=optional_string(arguments.get("situation_type")),
            lane=optional_string(arguments.get("lane")),
            actor=actor,
            policy=optional_string(arguments.get("policy")),
            keyword=optional_string(arguments.get("keyword")),
            task_ref=optional_string(arguments.get("task_ref")),
            limit=limit,
        ),
    )
    return tool_result(result.model_dump(mode="json"), f"Found {result.total} precedent(s)")


def _handle_lore_get_policy(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    policy_id = require_string(arguments.get("policy_id"), "policy_id")
    policy = ledger.get_policy(policy_id)
    if policy is None:
        return tool_result({"policy_id": policy_id}, f"Policy not found: {policy_id}", is_error=True)
    return tool_result(policy.model_dump(mode="json"), f"Retrieved policy: {policy.policy_id}")


def _handle_lore_upsert_page(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    page_id = require_string(arguments.get("page_id"), "page_id")
    content = require_string(arguments.get("content"), "content")
    try:
        page = ctx.repo.upsert_page(page_id, content)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(page)
    index_vector_page(ctx.vector_store, page)
    invalidate_graph_cache(ctx.graph_cache)
    return tool_result({"page": page.model_dump()}, f"Updated Lore page: {page.id}")


def _handle_lore_distill_daily(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    date_arg = optional_string(arguments.get("date"))
    actor_arg = optional_string(arguments.get("actor"))
    try:
        llm_client = getattr(getattr(getattr(ctx.request, "app", None), "state", None), "llm_client", None)
        result = distill_daily(
            ctx.repo,
            DailyDistillRequest(date=date_arg, actor=actor_arg),
            llm_client=llm_client,
        )
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None and result.page_id:
        page = ctx.repo.read_page(result.page_id)
        if page is not None:
            ctx.search_index.upsert_page_from_detail(page)
            index_vector_page(ctx.vector_store, page)
            invalidate_graph_cache(ctx.graph_cache)
    payload = result.model_dump()
    lines = [f"Distilled {result.capture_count} capture(s) for {result.date} -> {result.page_id}"]
    for cap in result.captures:
        lines.append(f"  - {cap.page_id}: {cap.title}")
    return tool_result(payload, "\n".join(lines))


def _handle_lore_get_daily(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    date_arg = require_string(arguments.get("date"), "date")
    try:
        from datetime import date as _date

        parsed = _date.fromisoformat(date_arg[:10])
    except ValueError as exc:
        raise JsonRpcError(-32602, "date must be ISO format YYYY-MM-DD.") from exc
    captures = get_daily_captures(ctx.repo, parsed)
    payload = {"date": date_arg, "captures": [c.model_dump() for c in captures]}
    lines = [f"{len(captures)} capture(s) for {date_arg}"]
    for cap in captures:
        lines.append(f"  - {cap.id}: {cap.title}")
    return tool_result(payload, "\n".join(lines))


def _handle_lore_promote_daily(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    date_arg = require_string(arguments.get("date"), "date")
    try:
        from datetime import date as _date

        parsed = _date.fromisoformat(date_arg[:10])
    except ValueError as exc:
        raise JsonRpcError(-32602, "date must be ISO format YYYY-MM-DD.") from exc
    try:
        page_id = promote_daily_note(ctx.repo, parsed)
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    return tool_result({"page_id": page_id, "status": "promoted"}, f"Promoted daily note: {page_id}")


def _handle_lore_heartbeat_review(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    _lint_config = _resolve_lint_config(ctx.repo)
    result = heartbeat_review(ctx.repo, _lint_config, ctx.graph_cache.get(ctx.repo) if ctx.graph_cache else None)
    payload = result.model_dump()
    return tool_result(payload, summarize_heartbeat(payload))


def _handle_lore_heartbeat_summary(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    _lint_config = _resolve_lint_config(ctx.repo)
    result = heartbeat_review(ctx.repo, _lint_config, ctx.graph_cache.get(ctx.repo) if ctx.graph_cache else None)
    payload = {
        cat: getattr(result, cat).count
        for cat in (
            "stale_pages",
            "missing_metadata",
            "contradictions",
            "low_confidence",
            "expired_facts",
            "procedure_issues",
        )
    }
    payload["total_issues"] = result.total_issues
    payload["generated_at"] = result.generated_at
    return tool_result(payload, summarize_heartbeat(payload))


def _handle_lore_heartbeat_audit(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    _lint_config = _resolve_lint_config(ctx.repo)
    captures = emit_heartbeat_captures(
        ctx.repo, _lint_config, ctx.graph_cache.get(ctx.repo) if ctx.graph_cache else None
    )
    for capture in captures:
        if ctx.search_index is not None:
            ctx.search_index.upsert_page_from_detail(capture)
        index_vector_page(ctx.vector_store, capture)
    invalidate_graph_cache(ctx.graph_cache)
    if ctx.metrics is not None:
        for _ in captures:
            ctx.metrics.increment_index_size()
    if ctx.audit_log is not None:
        from ..audit import new_audit_entry

        for capture in captures:
            ctx.audit_log.record(
                new_audit_entry(
                    actor="mcp:heartbeat_audit",
                    operation="heartbeat_capture",
                    page_id=capture.id,
                    summary=f"Captured {capture.title}",
                    diff_size=len(capture.content.encode("utf-8")),
                )
            )
    payload = {"captures": [capture.model_dump() for capture in captures]}
    return tool_result(payload, summarize_heartbeat_audit(payload))


def _handle_lore_find_repeated_captures(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    groups = find_repeated_captures(ctx.repo)
    payload = {"groups": [g.model_dump() for g in groups]}
    return tool_result(payload, summarize_repeated_captures(payload))


def _handle_lore_propose_procedure_candidate(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    capture_ids = string_arguments(arguments.get("capture_ids"))
    if len(capture_ids) < 2:
        raise JsonRpcError(-32602, "At least 2 capture IDs are required.")
    title = optional_string(arguments.get("title"))
    trigger = optional_string(arguments.get("trigger"))
    lane = optional_string(arguments.get("lane"))
    try:
        llm_client = getattr(getattr(getattr(ctx.request, "app", None), "state", None), "llm_client", None)
        result = propose_procedure_candidate(
            ctx.repo,
            capture_ids,
            title=title,
            trigger=trigger,
            lane=lane,
            llm_client=llm_client,
        )
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    except ValueError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    if ctx.search_index is not None:
        ctx.search_index.upsert_page_from_detail(result.page)
    index_vector_page(ctx.vector_store, result.page)
    invalidate_graph_cache(ctx.graph_cache)
    payload = {"page": result.page.model_dump(), "source_captures": [c.model_dump() for c in result.source_captures]}
    return tool_result(payload, result.message)


def _handle_lore_retry_extraction_deadletter(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    deadletter_id = require_string(arguments.get("deadletter_id"), "deadletter_id")
    app_state = getattr(getattr(ctx.request, "app", None), "state", None)
    llm_client = getattr(app_state, "llm_client", None)
    result = retry_deadletter(ctx.repo, deadletter_id, ledger_db=ledger, llm_client=llm_client)
    if result is None:
        raise JsonRpcError(-32602, f"Extraction dead-letter not found: {deadletter_id}")
    payload = result.model_dump()
    if result.resolved:
        text = f"Retried extraction dead-letter {deadletter_id}: produced {result.candidates} candidate(s)."
    else:
        detail = f" Error: {result.error}" if result.error else ""
        text = f"Retried extraction dead-letter {deadletter_id}, but it is not resolved yet.{detail}"
    return tool_result(payload, text, is_error=not result.resolved)


def _handle_lore_consolidation_status(ctx: McpContext) -> dict[str, Any]:
    tool_arguments(ctx.params)
    worker = require_service(ctx.consolidation_worker, "consolidation worker")
    payload = worker.status()
    return tool_result(payload, summarize_consolidation_status(payload))


def _handle_lore_consolidation_run(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    worker = require_service(ctx.consolidation_worker, "consolidation worker")
    result = worker.run(
        dry_run=bool(arguments.get("dry_run", True)),
        batch_size=max(1, min(int(arguments.get("batch_size") or 10), 50)),
        max_auto_apply=max(0, min(int(arguments.get("max_auto_apply") or 0), 100)),
        force_reextract=bool(arguments.get("force_reextract", False)),
    )
    payload = result.model_dump(mode="json")
    return tool_result(payload, summarize_consolidation_run(payload))


def _handle_lore_consolidation_rollback(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    worker = require_service(ctx.consolidation_worker, "consolidation worker")
    plan_id = require_string(arguments.get("plan_id"), "plan_id")
    try:
        result = worker.rollback(plan_id)
    except ValueError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    payload = result.model_dump(mode="json")
    return tool_result(payload, f"Rolled back plan {payload['plan_id']} on page {payload['page_id']}.")


def _handle_lore_list_patch_plans(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    status = optional_string(arguments.get("status"))
    valid_statuses = {"pending", "applied", "rejected", "rolled_back"}
    if status and status not in valid_statuses:
        raise JsonRpcError(-32602, f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}")
    limit = max(1, min(int(arguments.get("limit") or 100), 500))
    plans = ledger.list_patch_plans(
        status=status,
        target_page_id=optional_string(arguments.get("target_page_id")),
        limit=limit,
    )
    payload = {"count": len(plans), "plans": plans}
    return tool_result(payload, summarize_patch_plans(payload))


def _handle_lore_preview_patch(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    planner = require_service(ctx.patch_planner, "patch planner")
    plan_id = require_string(arguments.get("plan_id"), "plan_id")
    try:
        result = planner.preview_patch(plan_id)
    except ValueError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    payload = result.model_dump(mode="json")
    return tool_result(payload, summarize_patch_preview(payload))


def _handle_lore_apply_patch(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    planner = require_service(ctx.patch_planner, "patch planner")
    plan_id = require_string(arguments.get("plan_id"), "plan_id")
    try:
        result = planner.apply_plan(plan_id, force=bool(arguments.get("force", False)))
    except ValueError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    payload = result.model_dump(mode="json")
    return tool_result(
        payload,
        f"Applied patch {payload['plan_id']} to {payload['target_page_id']}. Operation: {payload['operation']}.",
    )


def _handle_lore_reject_patch(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    planner = require_service(ctx.patch_planner, "patch planner")
    plan_id = require_string(arguments.get("plan_id"), "plan_id")
    reason = optional_string(arguments.get("reason"))
    try:
        planner.reject_plan(plan_id, reason=reason)
    except ValueError as exc:
        raise JsonRpcError(-32602, str(exc)) from exc
    payload = {"plan_id": plan_id, "status": "rejected", "reason": reason}
    return tool_result(payload, f"Rejected patch plan {plan_id}.")


def _handle_lore_review_batch(ctx: McpContext) -> dict[str, Any]:
    arguments = tool_arguments(ctx.params)
    ledger = require_service(ctx.ledger_db, "ledger database")
    planner = require_service(ctx.patch_planner, "patch planner")
    batch_id = optional_string(arguments.get("batch_id"))
    payload = build_review_batch(ledger, planner, batch_id=batch_id)
    return tool_result(payload, summarize_review_batch(payload))


TOOL_HANDLERS: dict[str, Callable[[McpContext], dict[str, Any]]] = {
    "lore_overview": _handle_lore_overview,
    "lore_list_pages": _handle_lore_list_pages,
    "lore_read_page": _handle_lore_read_page,
    "lore_search": _handle_lore_search,
    "lore_list_lanes": _handle_lore_list_lanes,
    "lore_list_actors": _handle_lore_list_actors,
    "lore_rag_context": _handle_lore_rag_context,
    "lore_rag_context_expanded": _handle_lore_rag_context_expanded,
    "lore_recall": _handle_lore_recall,
    "lore_ack_recall": _handle_lore_ack_recall,
    "lore_link_graph": _handle_lore_link_graph,
    "lore_context_graph": _handle_lore_context_graph,
    "lore_graph_analytics": _handle_lore_graph_analytics,
    "lore_context_graph_neighbors": _handle_lore_context_graph_neighbors,
    "lore_context_graph_paths": _handle_lore_context_graph_paths,
    "lore_explain_context": _handle_lore_explain_context,
    "lore_page_links": _handle_lore_page_links,
    "lore_lint": _handle_lore_lint,
    "lore_stale_pages": _handle_lore_stale_pages,
    "lore_contradiction_review": _handle_lore_contradiction_review,
    "lore_frontmatter_spec": _handle_lore_frontmatter_spec,
    "lore_list_procedures": _handle_lore_list_procedures,
    "lore_create_procedure": _handle_lore_create_procedure,
    "lore_export_procedure": _handle_lore_export_procedure,
    "lore_capture": _handle_lore_capture,
    "lore_list_captures": _handle_lore_list_captures,
    "lore_capture_digest": _handle_lore_capture_digest,
    "lore_transition_capture": _handle_lore_transition_capture,
    "lore_promote_capture": _handle_lore_promote_capture,
    "lore_promotion_audit": _handle_lore_promotion_audit,
    "lore_create_stub": _handle_lore_create_stub,
    "lore_update_metadata": _handle_lore_update_metadata,
    "lore_ingest_service": _handle_lore_ingest_service,
    "lore_create_decision": _handle_lore_create_decision,
    "lore_create_trace": _handle_lore_create_trace,
    "lore_get_trace": _handle_lore_get_trace,
    "lore_get_provenance": _handle_lore_get_provenance,
    "lore_list_traces": _handle_lore_list_traces,
    "lore_list_policies": _handle_lore_list_policies,
    "lore_find_precedents": _handle_lore_find_precedents,
    "lore_get_policy": _handle_lore_get_policy,
    "lore_upsert_page": _handle_lore_upsert_page,
    "lore_distill_daily": _handle_lore_distill_daily,
    "lore_get_daily": _handle_lore_get_daily,
    "lore_promote_daily": _handle_lore_promote_daily,
    "lore_heartbeat_review": _handle_lore_heartbeat_review,
    "lore_heartbeat_summary": _handle_lore_heartbeat_summary,
    "lore_heartbeat_audit": _handle_lore_heartbeat_audit,
    "lore_find_repeated_captures": _handle_lore_find_repeated_captures,
    "lore_propose_procedure_candidate": _handle_lore_propose_procedure_candidate,
    "lore_retry_extraction_deadletter": _handle_lore_retry_extraction_deadletter,
    "lore_consolidation_status": _handle_lore_consolidation_status,
    "lore_consolidation_run": _handle_lore_consolidation_run,
    "lore_consolidation_rollback": _handle_lore_consolidation_rollback,
    "lore_list_patch_plans": _handle_lore_list_patch_plans,
    "lore_preview_patch": _handle_lore_preview_patch,
    "lore_apply_patch": _handle_lore_apply_patch,
    "lore_reject_patch": _handle_lore_reject_patch,
    "lore_review_batch": _handle_lore_review_batch,
}


def call_tool(
    repo: LoreRepository,
    params: dict[str, Any],
    search_index: Any | None = None,
    graph_cache: Any | None = None,
    vector_store: Any | None = None,
    code_inventories: dict[str, Any] | None = None,
    *,
    config: Any | None = None,
    ledger_db: Any | None = None,
    patch_planner: Any | None = None,
    consolidation_worker: Any | None = None,
    audit_log: Any | None = None,
    metrics: Any | None = None,
    request: Any | None = None,
) -> dict[str, Any]:
    name = require_string(params.get("name"), "name")
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise JsonRpcError(-32601, f"Unsupported Lore tool: {name}")
    ctx = McpContext(
        repo=repo,
        params=params,
        search_index=search_index,
        graph_cache=graph_cache,
        vector_store=vector_store,
        code_inventories=code_inventories if code_inventories is not None else {},
        config=config,
        ledger_db=ledger_db,
        patch_planner=patch_planner,
        consolidation_worker=consolidation_worker,
        audit_log=audit_log,
        metrics=metrics,
        request=request,
    )
    return handler(ctx)


def tool_result(structured_content: dict[str, Any], text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": is_error,
    }


def tool_arguments(params: dict[str, Any]) -> dict[str, Any]:
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "Tool arguments must be an object.")
    return arguments


def require_string(value: Any, name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise JsonRpcError(-32602, f"Missing required field: {name}")
    return cleaned


def optional_string(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def require_service(value: Any | None, name: str) -> Any:
    if value is None:
        raise JsonRpcError(-32603, f"Lore {name} is not configured.")
    return value


def _resolve_lint_config(repo: LoreRepository) -> LintConfig:
    from pathlib import Path

    config_path = Path(repo.root) / ".lore-lint.json"
    return LintConfig(config_path)


def string_arguments(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


def invalidate_graph_cache(graph_cache: Any | None) -> None:
    if graph_cache is not None:
        graph_cache.invalidate()


def index_vector_page(vector_store: Any | None, page: Any) -> None:
    if vector_store is None:
        return
    index_vectors_for_page(vector_store, page)


def enrich_rag_results(repo: LoreRepository, payload: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(payload)
    results = []
    for item in payload.get("results", []):
        result = dict(item)
        page = repo.read_page(str(result.get("page_id") or ""))
        if page is not None:
            result["title"] = page.title
            result["kind"] = page.kind
            result["visibility"] = page.visibility
            if not result.get("citations"):
                result["citations"] = [page.body[:200]]
        results.append(result)
    enriched["results"] = results
    enriched["total"] = len(results)
    return enriched


def enrich_expanded_results(repo: LoreRepository, payload: dict[str, Any]) -> dict[str, Any]:
    """Enrich expanded RAG results with titles from the repo, preserving expansion fields."""
    enriched = dict(payload)
    results = []
    for item in payload.get("results", []):
        result = dict(item)
        page = repo.read_page(str(result.get("page_id") or ""))
        if page is not None:
            result["title"] = page.title
        results.append(result)
    enriched["results"] = results
    enriched["total"] = len(results)
    return enriched


def filter_fts_hits(
    hits: list[dict[str, Any]],
    *,
    visibility: str | None = None,
    status: str | None = None,
    namespace: str | None = None,
    lane: str | None = None,
) -> list[dict[str, Any]]:
    return [
        hit
        for hit in hits
        if matches_page_filters(
            page_id=str(hit.get("page_id") or ""),
            visibility=hit.get("visibility"),
            status=hit.get("status"),
            visibility_filter=visibility,
            status_filter=status,
            namespace_filter=namespace,
        )
        and (lane is None or hit.get("lane") == lane)
    ]


def filter_repo_hits(
    hits: list[dict[str, Any]],
    *,
    status: str | None = None,
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    return [
        hit
        for hit in hits
        if matches_page_filters(
            page_id=str((hit.get("page") or {}).get("id") or ""),
            visibility=(hit.get("page") or {}).get("visibility"),
            status=(hit.get("page") or {}).get("status"),
            visibility_filter=None,
            status_filter=status,
            namespace_filter=namespace,
        )
    ]


def matches_page_filters(
    *,
    page_id: str,
    visibility: Any,
    status: Any,
    visibility_filter: str | None,
    status_filter: str | None,
    namespace_filter: str | None,
) -> bool:
    if visibility_filter and visibility != visibility_filter:
        return False
    if status_filter and status != status_filter:
        return False
    return not (namespace_filter and page_id.split("/", 1)[0] != namespace_filter)


def summarize_pages(pages: list[Any]) -> str:
    if not pages:
        return "No Lore pages found."
    lines = [f"{page.id} - {page.title} ({page.kind}, {page.visibility})" for page in pages]
    return "\n".join(lines)


def summarize_fts_search(payload: dict[str, Any]) -> str:
    query = payload.get("query", "")
    hits = payload.get("hits") or []
    lines = [f"Found {len(hits)} result(s) for '{query}':"]
    for hit in hits:
        fields = ", ".join(hit.get("matched_fields") or [])
        snippet = str(hit.get("snippet") or "")[:120]
        lines.append(f"  {hit.get('page_id', '?')} (score={hit.get('score', 0):.1f}, fields: {fields})")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def summarize_search(payload: dict[str, Any]) -> str:
    hits = payload.get("hits") or []
    query = payload.get("query", "")
    lines = [f"Found {len(hits)} result(s) for '{query}':"]
    for hit in hits:
        page = hit.get("page", {})
        lines.append(f"  {page.get('id', '?')} (score={hit.get('score', 0)})")
        for match in (hit.get("matches") or [])[:2]:
            lines.append(f"    {match[:100]}")
    return "\n".join(lines)


def summarize_traces(payload: dict[str, Any]) -> str:
    traces = payload.get("traces") or []
    lines = [f"Found {len(traces)} reasoning trace(s)."]
    for trace in traces[:10]:
        lines.append(f"  {trace.get('trace_id', '?')} - {trace.get('actor', '?')} ({trace.get('status', 'active')})")
    return "\n".join(lines)


def _recall_claim_payload(row: dict[str, Any]) -> dict[str, Any]:
    content = row.get("content_json") if isinstance(row.get("content_json"), dict) else {}
    return {
        "candidate_id": str(row.get("candidate_id") or ""),
        "subject": str(content.get("subject") or ""),
        "predicate": str(content.get("predicate") or ""),
        "object": str(content.get("object") or ""),
        "status": str(row.get("status") or "candidate"),
        "confidence": optional_string(row.get("confidence")),
        "actor": optional_string(row.get("actor")),
        "lane": optional_string(row.get("lane")),
        "strength": float(row.get("strength") or 0.0),
        "access_count": int(row.get("access_count") or 0),
        "age_days": float(row.get("age_days") or 0.0),
        "recall_score": float(row.get("recall_score") or 0.0),
        "recall_signals": row.get("recall_signals") if isinstance(row.get("recall_signals"), dict) else {},
        "source_page_ids": row.get("source_page_ids") if isinstance(row.get("source_page_ids"), list) else [],
    }


def summarize_recall(payload: dict[str, Any]) -> str:
    claims = payload.get("claims") or []
    query = payload.get("query") or "(no query)"
    lines = [f"Recalled {len(claims)} claim(s) for {query}:"]
    for claim in claims[:10]:
        subject = claim.get("subject") or "?"
        predicate = claim.get("predicate") or ""
        obj = claim.get("object") or ""
        lines.append(
            f"  [{claim.get('recall_score', 0):.3f}] {subject} {predicate} {obj}"
            f" (strength={claim.get('strength', 0):.2f}, accesses={claim.get('access_count', 0)})"
        )
    hint = payload.get("hint")
    if not claims and hint:
        lines.append(hint)
    return "\n".join(lines)


def summarize_rag_context(payload: dict[str, Any]) -> str:
    results = payload.get("results") or []
    query = payload.get("query", "")
    lines = [f"Found {len(results)} RAG result(s) for '{query}':"]
    for result in results:
        sources = ", ".join(result.get("sources") or [])
        title = result.get("title") or result.get("page_id") or "?"
        lines.append(
            f"  {result.get('page_id', '?')} - {title} (score={result.get('score', 0):.3f}, sources: {sources})"
        )
        citation = next((text for text in result.get("citations") or [] if text), "")
        if citation:
            lines.append(f"    {str(citation)[:120]}")
    return "\n".join(lines)


def summarize_link_graph(payload: dict[str, Any]) -> str:
    pages = payload.get("pages") or []
    links = payload.get("links") or []
    broken = payload.get("broken_links") or []
    lines = [f"{len(pages)} pages, {len(links)} links, {len(broken)} broken internal links."]
    for edge in broken[:10]:
        lines.append(f"broken: {edge['source']} -> {edge.get('target') or edge['href']}")
    return "\n".join(lines)


def summarize_page_links(payload: dict[str, Any]) -> str:
    page = payload["page"]
    outgoing = payload.get("outgoing") or []
    backlinks = payload.get("backlinks") or []
    missing = payload.get("missing_links") or []
    lines = [
        f"{page['id']} - {page['title']}",
        f"{len(backlinks)} backlinks, {len(outgoing)} outgoing links, {len(missing)} broken internal links.",
    ]
    for edge in backlinks[:10]:
        lines.append(f"backlink: {edge['source']} - {edge.get('source_title') or edge['source']}")
    for edge in missing[:10]:
        lines.append(f"broken: {edge.get('target') or edge['href']}")
    return "\n".join(lines)


def summarize_lint(payload: dict[str, Any]) -> str:
    issues = payload.get("issues") or []
    lines = [f"{payload.get('checked_pages', 0)} pages checked, {payload.get('issue_count', len(issues))} lint issues."]
    for issue in issues[:10]:
        page = issue.get("page_id") or "global"
        target = f" -> {issue['target']}" if issue.get("target") else ""
        lines.append(f"{issue['severity']}: {page}{target}: {issue['message']}")
    return "\n".join(lines)


def summarize_stale_pages(payload: dict[str, Any]) -> str:
    stale_pages = payload.get("stale_pages") or []
    missing_metadata = payload.get("missing_metadata_pages") or []
    lines = [
        f"{payload.get('total', len(stale_pages) + len(missing_metadata))} page(s) need freshness review.",
        f"Stale: {len(stale_pages)}, missing metadata: {len(missing_metadata)}.",
    ]
    for entry in stale_pages[:10]:
        lines.append(f"stale: {entry['page_id']} ({entry.get('days_stale')} days)")
    for entry in missing_metadata[:10]:
        lines.append(f"missing metadata: {entry['page_id']}")
    return "\n".join(lines)


def summarize_contradiction_review(payload: dict[str, Any]) -> str:
    contradictions = payload.get("contradictions") or []
    lines = [
        f"{payload.get('total_markers', len(contradictions))} contradiction marker(s) across {payload.get('total_pages', 0)} page(s)."
    ]
    for match in contradictions[:10]:
        lines.append(f"{match['page_id']}:{match['line_number']}: {match['matched_text']}")
    return "\n".join(lines)


def summarize_captures(payload: dict[str, Any]) -> str:
    captures = payload.get("captures") or []
    status = payload.get("status") or "all"
    if not captures:
        return f"No Lore captures found for status: {status}."
    lines = [f"{len(captures)} Lore captures for status: {status}."]
    for page in captures[:10]:
        lines.append(f"{page['id']} - {page['title']} ({page.get('status') or 'n/a'})")
    return "\n".join(lines)


def summarize_procedures(payload: dict[str, Any]) -> str:
    procedures = payload.get("procedures") or []
    if not procedures:
        return "No Lore procedures found."
    lines = [f"{len(procedures)} Lore procedure(s)."]
    for item in procedures[:10]:
        page = item.get("page") or {}
        trigger = item.get("trigger") or "no trigger"
        steps = item.get("steps") or []
        lines.append(f"{page.get('id', '?')} - {page.get('title', '?')}: {trigger} ({len(steps)} steps)")
    return "\n".join(lines)


def summarize_inventory(payload: dict[str, Any]) -> str:
    return (
        f"Ingested {payload.get('service_id', 'service')} code inventory: "
        f"{len(payload.get('routes') or [])} route(s), "
        f"{len(payload.get('symbols') or [])} symbol(s), "
        f"{len(payload.get('source_files') or [])} source file(s)."
    )


def summarize_heartbeat(payload: dict[str, Any]) -> str:
    total = payload.get("total_issues", 0)
    lines = [f"Heartbeat: {total} issue(s)."]
    categories = [
        ("stale_pages", "Stale pages"),
        ("missing_metadata", "Missing metadata"),
        ("contradictions", "Contradictions"),
        ("low_confidence", "Low confidence"),
        ("expired_facts", "Expired facts"),
        ("procedure_issues", "Procedure issues"),
    ]
    for key, label in categories:
        cat = payload.get(key)
        if cat is None:
            continue
        count = cat.get("count", 0) if isinstance(cat, dict) else cat
        if count:
            lines.append(f"  {label}: {count}")
    return "\n".join(lines)


def summarize_heartbeat_audit(payload: dict[str, Any]) -> str:
    captures = payload.get("captures") or []
    if not captures:
        return "Heartbeat audit created no new captures."
    return f"Heartbeat audit created {len(captures)} capture draft(s)."


def dumps_pretty(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def summarize_repeated_captures(payload: dict[str, Any]) -> str:
    groups = payload.get("groups") or []
    if not groups:
        return "No repeated capture patterns found."
    lines = [f"Found {len(groups)} repeated capture group(s)."]
    for group in groups[:10]:
        lines.append(
            f"  {group.get('suggested_title', '?')}: {group.get('count', 0)} captures (key: {group.get('group_key', '?')})"
        )
    return "\n".join(lines)


def summarize_consolidation_status(payload: dict[str, Any]) -> str:
    last_run = payload.get("last_run") or {}
    batch_id = last_run.get("batch_id") or "none"
    plans = payload.get("plans_by_status") or {}
    stuck = payload.get("stuck_runs") or []
    return (
        f"Last run: {batch_id}, Plans: {int(plans.get('pending') or 0)} pending, "
        f"{int(plans.get('applied') or 0)} applied, {int(plans.get('review_required') or 0)} audit-required. "
        f"{len(stuck)} stuck run(s)."
    )


def summarize_consolidation_run(payload: dict[str, Any]) -> str:
    return (
        f"Consolidation run {payload.get('batch_id')}: "
        f"{payload.get('captures_processed', 0)} captures, "
        f"{payload.get('candidates_extracted', 0)} candidates, "
        f"{payload.get('plans_generated', 0)} plans, "
        f"{payload.get('auto_applied', 0)} auto-applied, "
        f"{payload.get('review_required', 0)} requiring audit."
    )


def summarize_patch_plans(payload: dict[str, Any]) -> str:
    plans = payload.get("plans") or []
    lines = [f"{payload.get('count', len(plans))} patch plan(s)."]
    for plan in plans:
        lines.append(
            "  "
            f"{plan.get('plan_id')} {plan.get('target_page_id')} "
            f"{plan.get('operation')} risk={plan.get('risk_level')} "
            f"auto={bool(plan.get('auto_appliable'))} status={plan.get('status')}"
        )
    return "\n".join(lines)


def summarize_patch_preview(payload: dict[str, Any]) -> str:
    return (
        f"Patch preview for {payload.get('target_page_id')}:\n"
        f"Risk: {payload.get('risk_level')}, Auto-appliable: {payload.get('auto_appliable')}\n"
        f"{payload.get('unified_diff') or ''}"
    )


def build_review_batch(ledger: Any, planner: Any, *, batch_id: str | None = None) -> dict[str, Any]:
    plans = ledger.list_patch_plans(limit=500)
    if batch_id:
        plans = [plan for plan in plans if plan.get("batch_id") == batch_id]
    by_risk: dict[str, list[dict[str, Any]]] = {"low": [], "medium": [], "high": []}
    recommendations: list[str] = []

    for plan in plans:
        item = dict(plan)
        try:
            preview = planner.preview_patch(str(plan["plan_id"]))
            item["preview"] = {
                "unified_diff": preview.unified_diff,
                "current_content": preview.current_content,
                "proposed_content": preview.proposed_content,
            }
        except Exception as exc:  # pragma: no cover - defensive review helper
            item["preview_error"] = str(exc)

        risk = str(item.get("risk_level") or "medium")
        if risk not in by_risk:
            risk = "medium"
        by_risk[risk].append(item)
        recommendations.extend(recommendations_for_plan(item))

    auto_count = sum(1 for plan in plans if plan.get("auto_appliable"))
    return {
        "batch_id": batch_id,
        "total_plans": len(plans),
        "by_risk": by_risk,
        "auto_appliable_count": auto_count,
        "review_required_count": len(plans) - auto_count,
        "recommendations": unique_preserving_order(recommendations),
    }


def recommendations_for_plan(plan: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    target_page_id = str(plan.get("target_page_id") or "")
    risk = str(plan.get("risk_level") or "")
    operation = str(plan.get("operation") or "")
    if bool(plan.get("auto_appliable")) and risk == "low":
        recommendations.append("Auto-apply is safe. Consider applying with lore_consolidation_run or lore_apply_patch.")
    if risk == "medium":
        recommendations.append("Requires audit. Preview with lore_preview_patch before deciding.")
    if risk == "high" or target_page_id.startswith(("decisions/", "runbooks/")):
        recommendations.append("High risk or protected page. Requires explicit audit and force-apply.")
    if operation in {"update_existing_fact", "mark_stale"}:
        recommendations.append("Contains contradictions. Manual resolution recommended.")
    return recommendations


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def summarize_review_batch(payload: dict[str, Any]) -> str:
    by_risk = payload.get("by_risk") or {}
    batch_id = payload.get("batch_id") or "latest"
    lines = [
        f"Batch {batch_id}: {payload.get('total_plans', 0)} plans - "
        f"{payload.get('auto_appliable_count', 0)} auto-appliable, "
        f"{payload.get('review_required_count', 0)} need audit. "
        f"Low: {len(by_risk.get('low') or [])}, "
        f"Medium: {len(by_risk.get('medium') or [])}, "
        f"High: {len(by_risk.get('high') or [])}."
    ]
    if payload.get("review_required_count", 0):
        lines.append(f"Consider creating a Flow audit task for this batch using: flow_000XXX with batch_id={batch_id}")
    return "\n".join(lines)
