from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ..capture import build_capture_digest, build_promotion_audit, capture_memory, list_captures, promote_capture, slugify, transition_capture_status, unique_page_id
from ..context_graph import build_context_graph, explain_context, query_neighbors, query_paths
from ..distillation import distill_daily, get_daily_captures, get_pending_days, promote_daily_note
from ..procedure_candidate import find_repeated_captures, propose_procedure_candidate
from ..code_ingest.ingest_service import ingest_service_code
from ..frontmatter import frontmatter_scalar, update_frontmatter
from ..frontmatter_spec import get_frontmatter_spec
from ..provenance import get_capture_provenance, get_page_provenance
from ..link_graph import build_link_graph, page_links
from ..lint import lint_contradiction_review, lint_lore, lint_stale_queue
from ..lint_config import LintConfig
from ..heartbeat import heartbeat_review
from ..rag.chunker import chunk_page
from ..rag.hybrid_retrieval import hybrid_retrieve
from ..repository import InvalidPageId, LoreRepository
from ..schemas import (
    CaptureRequest,
    ContextExplainQuery,
    ContextGraphNeighborQuery,
    ContextGraphPathQuery,
    DailyDistillRequest,
    MetadataUpdate,
    TraceCreateRequest,
    TraceEntry,
    TraceListResponse,
)
from .decisions import build_decision_markdown, build_procedure_markdown, export_procedure_skill
from .dispatch import JsonRpcError

CODE_INVENTORIES: dict[str, dict[str, Any]] = {}
WRITE_TOOL_NAMES = {
    "lore_capture",
    "lore_apply_patch",
    "lore_consolidation_rollback",
    "lore_consolidation_run",
    "lore_create_trace",
    "lore_create_decision",
    "lore_propose_procedure_candidate",
    "lore_promote_capture",
    "lore_reject_patch",
    "lore_upsert_page",
    "lore_delete_page",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "lore_list_pages",
        "title": "List Lore Pages",
        "description": "List pages in Lore, optionally filtered by kind, visibility, or query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "Optional kind such as project, service, decision, or runbook."},
                "visibility": {"type": "string", "description": "Optional visibility such as public, internal, or private."},
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
                "namespace": {"type": "string", "description": "Filter by page namespace, such as projects or services."},
                "lane": {"type": "string", "enum": ["project", "procedural", "ops", "companion", "draft"], "description": "Filter by retrieval lane."},
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
            "properties": {},
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
            },
            "required": ["query"],
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
        "name": "lore_context_graph_neighbors",
        "title": "Lore Context Graph Neighbors",
        "description": "Find neighbors of a node in the Lore context graph, optionally filtered by direction, edge type, and node type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID to find neighbors for."},
                "direction": {"type": "string", "enum": ["outgoing", "incoming", "both"], "default": "both"},
                "edge_types": {"type": "array", "items": {"type": "string"}, "description": "Filter to these edge types."},
                "node_types": {"type": "array", "items": {"type": "string"}, "description": "Filter neighbors to these node types."},
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
                "edge_types": {"type": "array", "items": {"type": "string"}, "description": "Filter edges to these types."},
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
                "edge_types": {"type": "array", "items": {"type": "string"}, "description": "Filter edges to these types."},
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
                "reason_summary": {"type": "string", "description": "Concise human-readable rationale (max 5000 chars)."},
                "status": {"type": "string", "enum": ["active", "completed", "abandoned"], "description": "Trace status. Default: active."},
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
                "constraints": {"type": "array", "items": {"type": "string"}, "description": "Constraints that applied."},
                "policy_refs": {"type": "array", "items": {"type": "string"}, "description": "Policy IDs that governed the decision."},
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
                "outcome": {"type": "string", "description": "Outcome of the decision (fill in later if unknown at creation time)."},
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
            },
            "required": ["entity_type", "entity_id"],
        },
    },
    {
        "name": "lore_list_traces",
        "description": "Query reasoning traces by actor, status, task, or other filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string", "description": "Filter by actor name."},
                "status": {"type": "string", "description": "Filter by status (active, completed, abandoned)."},
                "task_id": {"type": "string", "description": "Filter by linked task ID."},
                "limit": {"type": "integer", "description": "Max results. Default: 20."},
            },
        },
    },
    {
        "name": "lore_list_policies",
        "title": "List Lore Policies",
        "description": "List policy rules that gate patch planning decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "gate": {"type": "string", "description": "Optional gate filter, such as auto-apply or protected-surface."},
                "enabled_only": {"type": "boolean", "default": True, "description": "Only return enabled policies."},
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


def call_tool(
    repo: LoreRepository,
    params: dict[str, Any],
    search_index: Any | None = None,
    graph_cache: Any | None = None,
    vector_store: Any | None = None,
    code_inventories: dict[str, Any] | None = None,
    *,
    ledger_db: Any | None = None,
    patch_planner: Any | None = None,
    consolidation_worker: Any | None = None,
) -> dict[str, Any]:
    name = require_string(params.get("name"), "name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "Tool arguments must be an object.")

    if name == "lore_list_pages":
        limit = int(arguments.get("limit") or 50)
        pages = repo.list_pages(
            kind=optional_string(arguments.get("kind")),
            visibility=optional_string(arguments.get("visibility")),
            q=optional_string(arguments.get("query")),
            limit=max(1, min(limit, 100)),
        )
        payload = {"pages": [page.model_dump() for page in pages]}
        return tool_result(payload, summarize_pages(pages))

    if name == "lore_read_page":
        page_id = require_string(arguments.get("page_id"), "page_id")
        page = repo.read_page(page_id)
        if page is None:
            return tool_result({"page_id": page_id}, f"Lore page not found: {page_id}", is_error=True)
        return tool_result({"page": page.model_dump()}, page.content)

    if name == "lore_search":
        query = require_string(arguments.get("query"), "query")
        limit = int(arguments.get("limit") or 20)
        limit_val = max(1, min(limit, 50))
        kind = optional_string(arguments.get("kind"))
        visibility = optional_string(arguments.get("visibility"))
        status = optional_string(arguments.get("status"))
        namespace = optional_string(arguments.get("namespace"))
        lane = optional_string(arguments.get("lane"))
        actor = optional_string(arguments.get("actor"))

        if search_index is not None:
            fts_hits = search_index.search(query, kind=kind, lane=lane, actor=actor, limit=50)
            if fts_hits or search_index_has_pages(search_index):
                hits = filter_fts_hits(fts_hits, visibility=visibility, status=status, namespace=namespace, lane=lane)[:limit_val]
                payload = {"query": query, "hits": hits}
                return tool_result(payload, summarize_fts_search(payload))

        results = repo.search(query, kind=kind, visibility=visibility, limit=limit_val)
        payload = results.model_dump()
        payload["hits"] = filter_repo_hits(payload["hits"], status=status, namespace=namespace)
        return tool_result(payload, summarize_search(payload))

    if name == "lore_list_lanes":
        if search_index is None:
            return tool_result({"lanes": []}, "Search index is not available.")
        lanes = search_index.list_lanes()
        return tool_result({"lanes": lanes}, f"Found {len(lanes)} retrieval lane(s).")

    if name == "lore_list_actors":
        if search_index is None:
            return tool_result({"actors": []}, "Search index is not available.")
        actors = search_index.list_actors()
        return tool_result({"actors": actors}, f"Found {len(actors)} known actor(s).")

    if name == "lore_rag_context":
        query = require_string(arguments.get("query"), "query")
        limit = max(1, min(int(arguments.get("limit") or 5), 20))
        if vector_store is None:
            return tool_result({"query": query, "results": []}, "RAG vector store is not configured.", is_error=True)
        graph = graph_cache.get(repo) if graph_cache is not None else None
        payload = enrich_rag_results(repo, hybrid_retrieve(query, search_index, vector_store, graph, limit=limit))
        return tool_result(payload, summarize_rag_context(payload))

    if name == "lore_link_graph":
        graph = build_link_graph(repo)
        payload = graph.model_dump()
        return tool_result(payload, summarize_link_graph(payload))

    if name == "lore_context_graph":
        ledger = require_service(ledger_db, "ledger database")
        graph = build_context_graph(repo, ledger)
        payload = graph.model_dump(mode="json")
        return tool_result(payload, f"Context graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")

    if name == "lore_context_graph_neighbors":
        node_id = require_string(arguments.get("node_id"), "node_id")
        direction = arguments.get("direction", "both")
        edge_types = arguments.get("edge_types", [])
        node_types = arguments.get("node_types", [])
        limit = max(1, min(int(arguments.get("limit", 50)), 500))
        ledger = require_service(ledger_db, "ledger database")
        graph = build_context_graph(repo, ledger)
        query = ContextGraphNeighborQuery(
            node_id=node_id,
            direction=direction,
            edge_types=edge_types,
            node_types=node_types,
            limit=limit,
        )
        result = query_neighbors(graph, query)
        return tool_result(result.model_dump(mode="json"), f"Found {result.total} neighbors for {node_id}")

    if name == "lore_context_graph_paths":
        source_id = require_string(arguments.get("source_id"), "source_id")
        target_id = require_string(arguments.get("target_id"), "target_id")
        max_depth = max(1, min(int(arguments.get("max_depth", 3)), 6))
        edge_types = arguments.get("edge_types", [])
        limit = max(1, min(int(arguments.get("limit", 10)), 50))
        ledger = require_service(ledger_db, "ledger database")
        graph = build_context_graph(repo, ledger)
        query = ContextGraphPathQuery(
            source_id=source_id,
            target_id=target_id,
            max_depth=max_depth,
            edge_types=edge_types,
            limit=limit,
        )
        result = query_paths(graph, query)
        return tool_result(result.model_dump(mode="json"), f"Found {len(result.paths)} paths from {source_id} to {target_id}")

    if name == "lore_explain_context":
        node_id = require_string(arguments.get("node_id"), "node_id")
        depth = max(1, min(int(arguments.get("depth", 2)), 3))
        edge_types = arguments.get("edge_types", [])
        ledger = require_service(ledger_db, "ledger database")
        graph = build_context_graph(repo, ledger)
        query = ContextExplainQuery(node_id=node_id, depth=depth, edge_types=edge_types)
        result = explain_context(graph, query)
        return tool_result(result.model_dump(mode="json"), result.explanation)

    if name == "lore_page_links":
        page_id = require_string(arguments.get("page_id"), "page_id")
        links = page_links(repo, page_id)
        if links is None:
            return tool_result({"page_id": page_id}, f"Lore page not found: {page_id}", is_error=True)
        payload = links.model_dump()
        return tool_result(payload, summarize_page_links(payload))

    if name == "lore_lint":
        lint = lint_lore(repo)
        payload = lint.model_dump()
        return tool_result(payload, summarize_lint(payload))

    if name == "lore_stale_pages":
        stale = lint_stale_queue(repo)
        payload = stale.model_dump()
        return tool_result(payload, summarize_stale_pages(payload))

    if name == "lore_contradiction_review":
        review = lint_contradiction_review(repo)
        payload = review.model_dump()
        return tool_result(payload, summarize_contradiction_review(payload))

    if name == "lore_frontmatter_spec":
        spec = get_frontmatter_spec()
        payload = spec.model_dump()
        return tool_result(payload, f"{len(payload['specs'])} frontmatter kind specs.")

    if name == "lore_list_procedures":
        pages = repo.list_pages(kind="procedure")
        procedures = []
        for summary in pages:
            page = repo.read_page(summary.id)
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

    if name == "lore_create_procedure":
        title = require_string(arguments.get("title"), "title")
        summary = require_string(arguments.get("summary"), "summary")
        trigger = require_string(arguments.get("trigger"), "trigger")
        steps = string_arguments(arguments.get("steps"))
        if not steps:
            raise JsonRpcError(-32602, "Missing required field: steps")
        preconditions = string_arguments(arguments.get("preconditions"))
        postconditions = string_arguments(arguments.get("postconditions"))
        error_handling = optional_string(arguments.get("error_handling")) or ""
        page_id = unique_page_id(repo, f"procedures/{slugify(title)}")
        content = build_procedure_markdown(
            title=title,
            summary=summary,
            trigger=trigger,
            steps=steps,
            preconditions=preconditions,
            postconditions=postconditions,
            error_handling=error_handling,
        )
        try:
            page = repo.upsert_page(page_id, content)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(page)
        index_vector_page(vector_store, page)
        invalidate_graph_cache(graph_cache)
        return tool_result({"page": page.model_dump()}, f"Created Lore procedure: {page.id}")

    if name == "lore_export_procedure":
        page_id = require_string(arguments.get("page_id"), "page_id")
        page = repo.read_page(page_id)
        if page is None or page.kind != "procedure":
            return tool_result({"page_id": page_id}, f"Lore procedure not found: {page_id}", is_error=True)
        content = export_procedure_skill(page)
        return tool_result({"page_id": page.id, "content": content}, content)

    if name == "lore_capture":
        try:
            page = capture_memory(repo, CaptureRequest(**arguments))
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        except ValidationError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(page)
        index_vector_page(vector_store, page)
        invalidate_graph_cache(graph_cache)
        payload = {"page": page.model_dump()}
        return tool_result(payload, f"Captured Lore memory: {page.id}")

    if name == "lore_list_captures":
        limit = int(arguments.get("limit") or 50)
        captures = list_captures(
            repo,
            status=optional_string(arguments.get("status")) or "draft",
            limit=max(1, min(limit, 200)),
        )
        payload = captures.model_dump()
        return tool_result(payload, summarize_captures(payload))

    if name == "lore_capture_digest":
        digest = build_capture_digest(repo)
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

    if name == "lore_transition_capture":
        page_id = require_string(arguments.get("page_id"), "page_id")
        new_status = require_string(arguments.get("status"), "status")
        valid = {"draft", "review", "accepted", "rejected", "archived"}
        if new_status not in valid:
            raise JsonRpcError(-32602, f"Invalid status. Must be one of: {', '.join(sorted(valid))}")
        try:
            page = transition_capture_status(repo, page_id, new_status)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(page)
        index_vector_page(vector_store, page)
        invalidate_graph_cache(graph_cache)
        return tool_result({"page": page.model_dump()}, f"Transitioned capture {page.id} to {new_status}.")

    if name == "lore_promote_capture":
        page_id = require_string(arguments.get("page_id"), "page_id")
        target = optional_string(arguments.get("target_page_id"))
        content = optional_string(arguments.get("content"))
        try:
            page = promote_capture(repo, page_id, target_page_id=target, content=content)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(page)
        index_vector_page(vector_store, page)
        invalidate_graph_cache(graph_cache)
        return tool_result({"page": page.model_dump()}, f"Promoted capture to {page.id}.")

    if name == "lore_promotion_audit":
        audit = build_promotion_audit(repo)
        payload = audit.model_dump()
        lines = []
        for rec in audit.promoted_captures:
            lines.append(f"{rec.capture_id} -> {rec.target_page_id} ({rec.capture_status})")
        for page in audit.pages_with_capture_sources:
            caps = ", ".join(capture.capture_id for capture in page.source_captures)
            lines.append(f"{page.page_id} sources: {caps}")
        return tool_result(payload, "\n".join(lines) or "No promotions recorded.")

    if name == "lore_create_stub":
        page_id = require_string(arguments.get("page_id"), "page_id")
        try:
            existing = repo.read_page(page_id)
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
            page = repo.upsert_page(page_id, stub_content)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(page)
        index_vector_page(vector_store, page)
        invalidate_graph_cache(graph_cache)
        return tool_result({"page": page.model_dump()}, f"Created Lore stub: {page.id}")

    if name == "lore_update_metadata":
        page_id = require_string(arguments.get("page_id"), "page_id")
        try:
            page = repo.read_page(page_id)
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
            updated_page = repo.upsert_page(page.id, updated_content)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(updated_page)
        index_vector_page(vector_store, updated_page)
        invalidate_graph_cache(graph_cache)
        return tool_result({"page": updated_page.model_dump()}, f"Updated Lore metadata: {updated_page.id}")

    if name == "lore_ingest_service":
        service_id = require_string(arguments.get("service_id"), "service_id")
        source_dir = require_string(arguments.get("source_dir"), "source_dir")
        inventory = ingest_service_code(service_id, source_dir)
        payload = inventory.model_dump()
        inventories = code_inventories if code_inventories is not None else CODE_INVENTORIES
        inventories[service_id] = payload
        return tool_result(payload, summarize_inventory(payload))

    if name == "lore_create_decision":
        title = require_string(arguments.get("title"), "title")
        summary = require_string(arguments.get("summary"), "summary")
        context = require_string(arguments.get("context"), "context")
        decision = require_string(arguments.get("decision"), "decision")
        consequences = require_string(arguments.get("consequences"), "consequences")
        deciders = string_arguments(arguments.get("deciders"))
        status = optional_string(arguments.get("status")) or "proposed"
        page_id = unique_page_id(repo, f"decisions/{slugify(title)}")
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
            page = repo.upsert_page(page_id, content)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(page)
        index_vector_page(vector_store, page)
        invalidate_graph_cache(graph_cache)
        return tool_result({"page": page.model_dump()}, f"Created Lore decision: {page.id}")

    if name == "lore_create_trace":
        ledger = require_service(ledger_db, "ledger database")
        try:
            payload = TraceCreateRequest(**arguments)
        except ValidationError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        trace = TraceEntry(trace_id="", **payload.model_dump())
        stored = ledger.store_trace(trace)
        content = stored.model_dump(mode="json")
        return tool_result(content, f"Created reasoning trace: {stored.trace_id}")

    if name == "lore_get_trace":
        ledger = require_service(ledger_db, "ledger database")
        trace_id = require_string(arguments.get("trace_id"), "trace_id")
        trace = ledger.get_trace(trace_id)
        if trace is None:
            return tool_result({"trace_id": trace_id}, f"Trace not found: {trace_id}", is_error=True)
        return tool_result(trace.model_dump(mode="json"), f"Retrieved reasoning trace: {trace.trace_id}")

    if name == "lore_get_provenance":
        entity_type = require_string(arguments.get("entity_type"), "entity_type")
        entity_id = require_string(arguments.get("entity_id"), "entity_id")
        if entity_type not in {"capture", "trace", "page"}:
            raise JsonRpcError(-32602, "entity_type must be one of: capture, trace, page")
        if entity_type == "capture":
            provenance = get_capture_provenance(repo, entity_id)
            if provenance is None:
                return tool_result({"entity_type": entity_type, "entity_id": entity_id}, f"Capture not found: {entity_id}", is_error=True)
        elif entity_type == "trace":
            ledger = require_service(ledger_db, "ledger database")
            trace = ledger.get_trace(entity_id)
            if trace is None:
                return tool_result({"entity_type": entity_type, "entity_id": entity_id}, f"Trace not found: {entity_id}", is_error=True)
            provenance = trace.provenance
        else:
            provenance = get_page_provenance(repo, entity_id)
        payload = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "provenance": (provenance.model_dump(mode="json") if provenance is not None else {}),
        }
        return tool_result(payload, f"Retrieved provenance for {entity_type}: {entity_id}")

    if name == "lore_list_traces":
        ledger = require_service(ledger_db, "ledger database")
        limit = max(1, min(int(arguments.get("limit") or 20), 500))
        filters = {
            "actor": optional_string(arguments.get("actor")),
            "status": optional_string(arguments.get("status")),
            "task_id": optional_string(arguments.get("task_id")),
        }
        traces = ledger.list_traces(**filters, limit=limit, offset=0)
        total = ledger.count_traces(**filters)
        response = TraceListResponse(traces=traces, total=total, limit=limit, offset=0)
        payload = response.model_dump(mode="json")
        return tool_result(payload, summarize_traces(payload))

    if name == "lore_list_policies":
        ledger = require_service(ledger_db, "ledger database")
        enabled_only = bool(arguments.get("enabled_only", True))
        policies = ledger.list_policies(
            gate=optional_string(arguments.get("gate")),
            enabled_only=enabled_only,
        )
        payload = {"count": len(policies), "policies": [policy.model_dump(mode="json") for policy in policies]}
        return tool_result(payload, f"Found {len(policies)} policy rule(s).")

    if name == "lore_get_policy":
        ledger = require_service(ledger_db, "ledger database")
        policy_id = require_string(arguments.get("policy_id"), "policy_id")
        policy = ledger.get_policy(policy_id)
        if policy is None:
            return tool_result({"policy_id": policy_id}, f"Policy not found: {policy_id}", is_error=True)
        return tool_result(policy.model_dump(mode="json"), f"Retrieved policy: {policy.policy_id}")

    if name == "lore_upsert_page":
        page_id = require_string(arguments.get("page_id"), "page_id")
        content = require_string(arguments.get("content"), "content")
        try:
            page = repo.upsert_page(page_id, content)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(page)
        index_vector_page(vector_store, page)
        invalidate_graph_cache(graph_cache)
        return tool_result({"page": page.model_dump()}, f"Updated Lore page: {page.id}")

    if name == "lore_distill_daily":
        date_arg = optional_string(arguments.get("date"))
        actor_arg = optional_string(arguments.get("actor"))
        try:
            result = distill_daily(repo, DailyDistillRequest(date=date_arg, actor=actor_arg))
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None and result.page_id:
            page = repo.read_page(result.page_id)
            if page is not None:
                search_index.upsert_page_from_detail(page)
                index_vector_page(vector_store, page)
                invalidate_graph_cache(graph_cache)
        payload = result.model_dump()
        lines = [f"Distilled {result.capture_count} capture(s) for {result.date} -> {result.page_id}"]
        for cap in result.captures:
            lines.append(f"  - {cap.page_id}: {cap.title}")
        return tool_result(payload, "\n".join(lines))

    if name == "lore_get_daily":
        date_arg = require_string(arguments.get("date"), "date")
        try:
            from datetime import date as _date
            parsed = _date.fromisoformat(date_arg[:10])
        except ValueError as exc:
            raise JsonRpcError(-32602, "date must be ISO format YYYY-MM-DD.") from exc
        captures = get_daily_captures(repo, parsed)
        payload = {"date": date_arg, "captures": [c.model_dump() for c in captures]}
        lines = [f"{len(captures)} capture(s) for {date_arg}"]
        for cap in captures:
            lines.append(f"  - {cap.id}: {cap.title}")
        return tool_result(payload, "\n".join(lines))

    if name == "lore_promote_daily":
        date_arg = require_string(arguments.get("date"), "date")
        try:
            from datetime import date as _date
            parsed = _date.fromisoformat(date_arg[:10])
        except ValueError as exc:
            raise JsonRpcError(-32602, "date must be ISO format YYYY-MM-DD.") from exc
        try:
            page_id = promote_daily_note(repo, parsed)
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        return tool_result({"page_id": page_id, "status": "promoted"}, f"Promoted daily note: {page_id}")

    if name == "lore_heartbeat_review":
        _lint_config = _resolve_lint_config(repo)
        result = heartbeat_review(repo, _lint_config, graph_cache.get(repo) if graph_cache else None)
        payload = result.model_dump()
        return tool_result(payload, summarize_heartbeat(payload))

    if name == "lore_heartbeat_summary":
        _lint_config = _resolve_lint_config(repo)
        result = heartbeat_review(repo, _lint_config, graph_cache.get(repo) if graph_cache else None)
        payload = {cat: getattr(result, cat).count for cat in (
            "stale_pages", "missing_metadata", "contradictions",
            "low_confidence", "expired_facts", "procedure_issues",
        )}
        payload["total_issues"] = result.total_issues
        payload["generated_at"] = result.generated_at
        return tool_result(payload, summarize_heartbeat(payload))

    if name == "lore_find_repeated_captures":
        groups = find_repeated_captures(repo)
        payload = {"groups": [g.model_dump() for g in groups]}
        return tool_result(payload, summarize_repeated_captures(payload))

    if name == "lore_propose_procedure_candidate":
        capture_ids = string_arguments(arguments.get("capture_ids"))
        if len(capture_ids) < 2:
            raise JsonRpcError(-32602, "At least 2 capture IDs are required.")
        title = optional_string(arguments.get("title"))
        trigger = optional_string(arguments.get("trigger"))
        lane = optional_string(arguments.get("lane"))
        try:
            result = propose_procedure_candidate(
                repo, capture_ids, title=title, trigger=trigger, lane=lane,
            )
        except InvalidPageId as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        if search_index is not None:
            search_index.upsert_page_from_detail(result.page)
        index_vector_page(vector_store, result.page)
        invalidate_graph_cache(graph_cache)
        payload = {"page": result.page.model_dump(), "source_captures": [c.model_dump() for c in result.source_captures]}
        return tool_result(payload, result.message)

    if name == "lore_consolidation_status":
        worker = require_service(consolidation_worker, "consolidation worker")
        payload = worker.status()
        return tool_result(payload, summarize_consolidation_status(payload))

    if name == "lore_consolidation_run":
        worker = require_service(consolidation_worker, "consolidation worker")
        result = worker.run(
            dry_run=bool(arguments.get("dry_run", True)),
            batch_size=max(1, min(int(arguments.get("batch_size") or 10), 50)),
            max_auto_apply=max(0, min(int(arguments.get("max_auto_apply") or 0), 100)),
            force_reextract=bool(arguments.get("force_reextract", False)),
        )
        payload = result.model_dump(mode="json")
        return tool_result(payload, summarize_consolidation_run(payload))

    if name == "lore_consolidation_rollback":
        worker = require_service(consolidation_worker, "consolidation worker")
        plan_id = require_string(arguments.get("plan_id"), "plan_id")
        try:
            result = worker.rollback(plan_id)
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        payload = result.model_dump(mode="json")
        return tool_result(payload, f"Rolled back plan {payload['plan_id']} on page {payload['page_id']}.")

    if name == "lore_list_patch_plans":
        ledger = require_service(ledger_db, "ledger database")
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

    if name == "lore_preview_patch":
        planner = require_service(patch_planner, "patch planner")
        plan_id = require_string(arguments.get("plan_id"), "plan_id")
        try:
            result = planner.preview_patch(plan_id)
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        payload = result.model_dump(mode="json")
        return tool_result(payload, summarize_patch_preview(payload))

    if name == "lore_apply_patch":
        planner = require_service(patch_planner, "patch planner")
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

    if name == "lore_reject_patch":
        planner = require_service(patch_planner, "patch planner")
        plan_id = require_string(arguments.get("plan_id"), "plan_id")
        reason = optional_string(arguments.get("reason"))
        try:
            planner.reject_plan(plan_id, reason=reason)
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        payload = {"plan_id": plan_id, "status": "rejected", "reason": reason}
        return tool_result(payload, f"Rejected patch plan {plan_id}.")

    if name == "lore_review_batch":
        ledger = require_service(ledger_db, "ledger database")
        planner = require_service(patch_planner, "patch planner")
        batch_id = optional_string(arguments.get("batch_id"))
        payload = build_review_batch(ledger, planner, batch_id=batch_id)
        return tool_result(payload, summarize_review_batch(payload))

    raise JsonRpcError(-32602, f"Unknown Lore tool: {name}")


def tool_result(structured_content: dict[str, Any], text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": is_error,
    }


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
    config_path = Path(repo.content_dir) / ".lore-lint.json"
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
    vector_store.remove_page(page.id)
    for chunk in chunk_page(page.id, page.content, page.body):
        vector_store.upsert_chunk(
            chunk["chunk_id"],
            chunk["page_id"],
            chunk["chunk_index"],
            chunk["content"],
        )
    vector_store.rebuild_doc_freq()


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


def search_index_has_pages(search_index: Any) -> bool:
    try:
        row = search_index._conn.execute("SELECT 1 FROM pages LIMIT 1").fetchone()
    except Exception:
        return True
    return row is not None


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
    if namespace_filter and page_id.split("/", 1)[0] != namespace_filter:
        return False
    return True


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


def summarize_rag_context(payload: dict[str, Any]) -> str:
    results = payload.get("results") or []
    query = payload.get("query", "")
    lines = [f"Found {len(results)} RAG result(s) for '{query}':"]
    for result in results:
        sources = ", ".join(result.get("sources") or [])
        title = result.get("title") or result.get("page_id") or "?"
        lines.append(f"  {result.get('page_id', '?')} - {title} (score={result.get('score', 0):.3f}, sources: {sources})")
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


def dumps_pretty(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def summarize_repeated_captures(payload: dict[str, Any]) -> str:
    groups = payload.get("groups") or []
    if not groups:
        return "No repeated capture patterns found."
    lines = [f"Found {len(groups)} repeated capture group(s)."]
    for group in groups[:10]:
        lines.append(f"  {group.get('suggested_title', '?')}: {group.get('count', 0)} captures (key: {group.get('group_key', '?')})")
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
