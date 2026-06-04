from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PageSummary(BaseModel):
    id: str
    title: str
    kind: str
    visibility: str
    status: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    source_task: str | None = None
    decision_id: str | None = None
    trace_id: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    policies_applied: list[str] = Field(default_factory=list)
    epistemic_status: EpistemicStatus | None = None
    updated_at: str
    size: int


class PageDetail(PageSummary):
    content: str
    body: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class LoreApiKeyRole(StrEnum):
    admin = "admin"
    writer = "writer"
    reader = "reader"


class LoreApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    role: LoreApiKeyRole = LoreApiKeyRole.writer


class LoreApiKeyResponse(BaseModel):
    id: str
    name: str
    description: str
    role: str
    key_prefix: str
    created_at: str
    revoked_at: str | None = None


class LoreApiKeyCreateResponse(LoreApiKeyResponse):
    api_key: str


class LlmSettingsUpdate(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    escalation_model: str | None = None
    escalation_api_key: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None


class LlmSettingsResponse(BaseModel):
    provider: str
    model: str
    base_url: str
    api_key_configured: bool
    api_key_hint: str
    escalation_model: str
    escalation_api_key_configured: bool
    escalation_api_key_hint: str
    max_tokens: int
    temperature: float
    timeout_seconds: float
    max_retries: int


class FrontmatterKindSpec(BaseModel):
    kind: str
    required: list[str]
    optional: list[str]


class FrontmatterSpecResponse(BaseModel):
    specs: dict[str, FrontmatterKindSpec]
    all_fields: list[str]


class MetadataUpdate(BaseModel):
    owner: str | None = None
    reviewed_at: str | None = None
    stale_after: str | None = None
    confidence: str | None = None
    observed_at: str | None = Field(default=None, description="When the fact was observed (capture time).")
    valid_from: str | None = Field(default=None, description="When the fact became true in the world.")
    valid_until: str | None = Field(default=None, description="When the fact ceased to be true (null = still valid).")
    epistemic_status: str | None = None
    status: str | None = None


class TocEntry(BaseModel):
    level: int
    id: str
    title: str


class RenderedLink(BaseModel):
    href: str
    label: str | None = None
    page_id: str | None = None
    exists: bool
    external: bool = False


class PageRendered(PageSummary):
    html: str
    toc: list[TocEntry] = Field(default_factory=list)
    links: list[RenderedLink] = Field(default_factory=list)
    missing_links: list[RenderedLink] = Field(default_factory=list)


class LinkEdge(BaseModel):
    source: str
    source_title: str | None = None
    target: str | None = None
    target_title: str | None = None
    href: str
    label: str | None = None
    exists: bool
    external: bool = False
    relationship_type: str = "wikilink"


class LinkGraphResponse(BaseModel):
    pages: list[PageSummary]
    links: list[LinkEdge]
    broken_links: list[LinkEdge] = Field(default_factory=list)


class GraphNode(BaseModel):
    page_id: str
    title: str
    kind: str
    visibility: str
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    inbound_count: int = 0
    outbound_count: int = 0


class EnrichedLinkGraphResponse(BaseModel):
    nodes: list[GraphNode]
    links: list[LinkEdge]
    broken_links: list[LinkEdge] = Field(default_factory=list)


class ContextNodeType(StrEnum):
    page = "page"
    capture = "capture"
    entity = "entity"
    claim = "claim"
    invalidation = "invalidation"
    plan = "plan"
    trace = "trace"
    actor = "actor"
    task = "task"
    policy = "policy"
    source = "source"
    tool = "tool"


class ContextEdgeType(StrEnum):
    provenance = "provenance"
    mentions = "mentions"
    supports = "supports"
    contradicts = "contradicts"
    supersedes = "supersedes"
    generated = "generated"
    applied = "applied"
    used_policy = "used-policy"
    source_of = "source-of"
    task_related = "task-related"
    parent = "parent"
    authored = "authored"
    used_tool = "used-tool"


class ContextGraphNode(BaseModel):
    """A node in the context graph spanning all Lore entity types."""

    id: str
    type: ContextNodeType
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextGraphEdge(BaseModel):
    """A typed directed edge in the context graph."""

    source: str
    target: str
    type: ContextEdgeType
    label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextGraph(BaseModel):
    """Complete context graph over all Lore entities."""

    nodes: list[ContextGraphNode] = Field(default_factory=list)
    edges: list[ContextGraphEdge] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)


class ContextGraphNeighborQuery(BaseModel):
    """Query for neighbors of a specific node."""

    node_id: str = Field(..., description="Node ID to find neighbors for.")
    direction: Literal["outgoing", "incoming", "both"] = Field(default="both", description="Edge direction to follow.")
    edge_types: list[str] = Field(default_factory=list, description="Filter to these edge types. Empty = all.")
    node_types: list[str] = Field(
        default_factory=list, description="Filter neighbor nodes to these types. Empty = all."
    )
    limit: int = Field(default=50, ge=1, le=500, description="Max neighbors to return.")


class ContextGraphNeighbor(BaseModel):
    """A neighbor node with the connecting edge."""

    node: ContextGraphNode
    edge: ContextGraphEdge


class ContextGraphNeighborResponse(BaseModel):
    """Response for a neighbor query."""

    node_id: str
    neighbors: list[ContextGraphNeighbor]
    total: int


class PrecedentSearchRequest(BaseModel):
    """Search for precedents across traces, decisions, policies, captures, and pages."""

    entity: str | None = Field(default=None, description="Entity name (e.g. 'Lore', 'routing').")
    situation_type: str | None = Field(
        default=None,
        description="Situation type filter (e.g. 'consolidation', 'review', 'rollback').",
    )
    lane: str | None = Field(
        default=None, description="Retrieval lane filter (project, procedural, ops, companion, draft)."
    )
    actor: str | None = Field(default=None, description="Agent name filter.")
    policy: str | None = Field(default=None, description="Policy ID filter (e.g. 'auto-apply:v1').")
    keyword: str | None = Field(default=None, description="Keyword filter for reason summaries and page titles.")
    task_ref: str | None = Field(default=None, description="Task ID reference (e.g. 'flow_000123').")
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return.")


class PrecedentResult(BaseModel):
    """A single precedent match."""

    type: str = Field(description="Result type: trace, decision, page, policy, or capture.")
    id: str = Field(description="Entity ID.")
    title: str = Field(default="", description="Human-readable title.")
    summary: str = Field(default="", description="Outcome or reason summary.")
    outcome: str = Field(default="", description="Result outcome or status.")
    actor: str = Field(default="", description="Agent that produced this.")
    related_policies: list[str] = Field(default_factory=list, description="Policy IDs referenced.")
    graph_paths: list[dict[str, str]] = Field(
        default_factory=list, description="Context graph paths from this entity to related context."
    )
    relevance: float = Field(default=0.0, description="Relevance score.")


class PrecedentSearchResponse(BaseModel):
    """Response for precedent search."""

    matches: list[PrecedentResult] = Field(default_factory=list)
    total: int


class ContextGraphPathQuery(BaseModel):
    """Query for bounded paths between two nodes."""

    source_id: str = Field(..., description="Start node ID.")
    target_id: str = Field(..., description="Target node ID.")
    max_depth: int = Field(default=3, ge=1, le=6, description="Maximum path length.")
    edge_types: list[str] = Field(default_factory=list, description="Filter edges to these types. Empty = all.")
    limit: int = Field(default=10, ge=1, le=50, description="Max paths to return.")


class ContextGraphPathStep(BaseModel):
    """One step in a graph path."""

    edge: ContextGraphEdge
    node: ContextGraphNode


class ContextGraphPath(BaseModel):
    """A path through the context graph."""

    source_id: str
    target_id: str
    steps: list[ContextGraphPathStep]
    length: int


class ContextGraphPathResponse(BaseModel):
    """Response for a path query."""

    source_id: str
    target_id: str
    paths: list[ContextGraphPath]


class ContextExplainQuery(BaseModel):
    """Query for explaining the context around a node."""

    node_id: str = Field(..., description="Node to explain.")
    depth: int = Field(default=2, ge=1, le=3, description="Neighborhood depth.")
    edge_types: list[str] = Field(default_factory=list, description="Filter edges to these types. Empty = all.")


class ContextExplainResponse(BaseModel):
    """Explanation of context around a node."""

    node: ContextGraphNode
    neighborhood: list[ContextGraphNeighbor]
    explanation: str


class StubRequest(BaseModel):
    title: str | None = None
    kind: str = "page"
    source_page: str | None = None


class LintIssue(BaseModel):
    rule: str
    severity: Literal["error", "warning", "info"]
    page_id: str | None = None
    title: str | None = None
    message: str
    target: str | None = None
    detail: str | None = None
    suggestion: str | None = None
    auto_fixable: bool = False
    suppressed: bool = False
    suppression_reason: str | None = None


class LoreLintResponse(BaseModel):
    checked_pages: int
    issue_count: int
    suppressed_count: int = 0
    issues: list[LintIssue] = Field(default_factory=list)


class StalePageEntry(BaseModel):
    page_id: str
    title: str
    kind: str
    stale_after: str | None = None
    reviewed_at: str | None = None
    days_stale: int | None = None
    severity: Literal["stale", "missing_metadata"]


class StalePagesResponse(BaseModel):
    total: int
    stale_pages: list[StalePageEntry] = Field(default_factory=list)
    missing_metadata_pages: list[StalePageEntry] = Field(default_factory=list)


class ContradictionMatch(BaseModel):
    page_id: str
    title: str
    line_number: int
    matched_text: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)


class ContradictionReviewResponse(BaseModel):
    total_pages: int
    total_markers: int
    contradictions: list[ContradictionMatch] = Field(default_factory=list)


class ProvenanceRef(BaseModel):
    """A unified provenance reference linking a capture/trace/output to its influences."""

    page_ids: list[str] = Field(default_factory=list, description="Lore page IDs that informed this output.")
    capture_ids: list[str] = Field(default_factory=list, description="Capture page IDs that contributed.")
    trace_ids: list[str] = Field(default_factory=list, description="Reasoning trace IDs.")
    policy_ids: list[str] = Field(default_factory=list, description="Policy IDs that were evaluated.")
    candidate_ids: list[str] = Field(default_factory=list, description="Extraction candidate IDs.")
    task_ids: list[str] = Field(default_factory=list, description="Flow or external task IDs.")
    source_paths: list[str] = Field(default_factory=list, description="Repo or file paths.")
    source_urls: list[str] = Field(default_factory=list, description="HTTP/HTTPS URLs.")
    source_task: str | None = Field(default=None, description="Legacy source task identifier.")
    actor: str | None = Field(default=None, description="Agent that produced this output.")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, description="Tool calls made during production.")
    constraints: list[str] = Field(default_factory=list, description="Constraints that applied.")


class ProvenanceResponse(BaseModel):
    entity_type: str
    entity_id: str
    provenance: ProvenanceRef


class CaptureRequest(BaseModel):
    observation: str = Field(min_length=1)
    title: str | None = None
    namespace: Literal["inbox", "notes"] = "inbox"
    agent: str | None = None
    capture_date: str | None = None
    source_task: str | None = None
    related_pages: list[str] = Field(default_factory=list)
    confidence: str | None = "unknown"
    suggested_target_page: str | None = None
    sources: list[str] = Field(default_factory=list)
    source_paths: list[str] = Field(default_factory=list, description="Repo paths or file references.")
    source_urls: list[str] = Field(default_factory=list, description="HTTP/HTTPS URLs.")
    evidence: str | None = Field(default=None, description="Supporting evidence text.")
    epistemic_status: EpistemicStatus | None = Field(default=None, description="How this knowledge was obtained.")
    actor: str | None = Field(default=None, description="Agent name that produced this capture.")
    lane: str | None = Field(default=None, description="Retrieval lane: project, procedural, ops, companion, draft.")
    observed_at: str | None = Field(default=None, description="When the fact was observed (ISO timestamp).")
    valid_from: str | None = Field(default=None, description="When the fact became true in the world (ISO date).")
    valid_until: str | None = Field(default=None, description="When the fact ceased to be true (null = still valid).")
    task_id: str | None = Field(default=None, description="Source task ID (e.g. flow_000123).")
    decision_id: str | None = Field(default=None, description="Linked decision page ID.")
    trace_id: str | None = Field(default=None, description="Reasoning trace correlation ID.")
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tool call records from the capturing session.",
    )
    constraints: list[str] = Field(default_factory=list, description="Constraints that applied during capture.")
    policies_applied: list[str] = Field(default_factory=list, description="Policy IDs that were enforced.")
    provenance: ProvenanceRef | None = Field(default=None, description="Unified provenance references.")


class MemoryCaptureRequest(BaseModel):
    text: str = Field(min_length=1)
    agent_name: str | None = Field(default=None, description="Agent name for notes namespace.")
    namespace: Literal["inbox", "notes"] = "inbox"
    tags: list[str] = Field(default_factory=list)
    lane: str | None = Field(default=None, description="Retrieval lane: project, procedural, ops, companion, draft.")
    actor: str | None = Field(default=None, description="Agent name for provenance.")
    task_id: str | None = Field(default=None, description="Source task ID (e.g. flow_000123).")
    decision_id: str | None = Field(default=None, description="Linked decision page ID.")
    trace_id: str | None = Field(default=None, description="Reasoning trace correlation ID.")
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tool call records from the capturing session.",
    )
    constraints: list[str] = Field(default_factory=list, description="Constraints that applied during capture.")
    policies_applied: list[str] = Field(default_factory=list, description="Policy IDs that were enforced.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extra frontmatter fields.")


class MemoryCaptureResponse(BaseModel):
    capture_id: str
    timestamp: str


class ContextRef(BaseModel):
    """Reference to a context entity involved in a reasoning trace."""

    type: Literal["page", "capture", "task", "candidate", "plan", "trace", "policy", "source", "actor", "tool"]
    id: str


class ToolRef(BaseModel):
    """Tool call reference in a reasoning trace."""

    tool: str
    action: str = ""
    result_summary: str = ""


class Alternative(BaseModel):
    """An alternative considered but not chosen."""

    description: str
    rejected_reason: str = ""


class TraceCreateRequest(BaseModel):
    """Create a new reasoning trace."""

    parent_trace_id: str | None = None
    actor: str = Field(min_length=1, max_length=120)
    reason_summary: str = Field(min_length=1, max_length=5000)
    lane: str | None = Field(default=None, description="Retrieval lane (project, ops, research).")
    status: Literal["active", "completed", "abandoned"] = "active"
    context_refs: list[ContextRef] = Field(default_factory=list)
    tool_refs: list[ToolRef] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    provenance: ProvenanceRef | None = Field(default=None, description="Unified provenance references.")
    epistemic_status: EpistemicStatus | None = Field(default=None, description="How this trace knowledge was obtained.")
    outcome: str = ""
    related_ids: dict[str, str] = Field(default_factory=dict)


class TraceUpdateRequest(BaseModel):
    """Update an existing trace (e.g., add outcome, change status)."""

    reason_summary: str | None = None
    status: Literal["active", "completed", "abandoned"] | None = None
    context_refs: list[ContextRef] | None = None
    tool_refs: list[ToolRef] | None = None
    constraints: list[str] | None = None
    policy_refs: list[str] | None = None
    alternatives: list[Alternative] | None = None
    provenance: ProvenanceRef | None = None
    epistemic_status: EpistemicStatus | None = None
    outcome: str | None = None
    related_ids: dict[str, str] | None = None


class EpistemicStatus(StrEnum):
    """How was this knowledge obtained?"""

    operator_declared = "operator_declared"
    retrieved = "retrieved"
    inferred = "inferred"
    assumption = "assumption"
    hearsay = "hearsay"


class TraceEntry(BaseModel):
    """A stored reasoning trace."""

    trace_id: str
    parent_trace_id: str | None = None
    actor: str
    reason_summary: str
    lane: str | None = Field(default=None, description="Retrieval lane (project, ops, research).")
    status: str = "active"
    context_refs: list[ContextRef] = Field(default_factory=list)
    tool_refs: list[ToolRef] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    provenance: ProvenanceRef | None = Field(default=None, description="Unified provenance references.")
    epistemic_status: EpistemicStatus | None = None
    outcome: str = ""
    related_ids: dict[str, str] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class TraceListResponse(BaseModel):
    traces: list[TraceEntry]
    total: int
    limit: int
    offset: int


class CaptureStatusUpdate(BaseModel):
    status: Literal["draft", "review", "accepted", "rejected", "archived"]


class CapturePromotion(BaseModel):
    target_page_id: str | None = None
    content: str | None = None


class CaptureResponse(BaseModel):
    page: PageDetail


class CaptureListResponse(BaseModel):
    status: str | None = None
    count: int
    captures: list[PageSummary] = Field(default_factory=list)


class CaptureDigestGroup(BaseModel):
    """A group of captures sharing a common attribute."""

    key: str
    label: str | None = None
    count: int
    captures: list[PageSummary] = Field(default_factory=list)


class CaptureDigestResponse(BaseModel):
    total_draft: int = 0
    total_review: int = 0
    by_date: list[CaptureDigestGroup] = Field(default_factory=list)
    by_source_task: list[CaptureDigestGroup] = Field(default_factory=list)
    by_suggested_target: list[CaptureDigestGroup] = Field(default_factory=list)


class PromotionRecord(BaseModel):
    capture_id: str
    capture_title: str | None = None
    target_page_id: str
    capture_status: str | None = None


class SourceCapture(BaseModel):
    capture_id: str
    capture_title: str | None = None
    capture_status: str | None = None


class PagePromotionSource(BaseModel):
    page_id: str
    page_title: str | None = None
    source_captures: list[SourceCapture] = Field(default_factory=list)


class PromotionAuditResponse(BaseModel):
    promoted_captures: list[PromotionRecord] = Field(default_factory=list)
    pages_with_capture_sources: list[PagePromotionSource] = Field(default_factory=list)


class PageLinks(BaseModel):
    page: PageSummary
    outgoing: list[LinkEdge] = Field(default_factory=list)
    backlinks: list[LinkEdge] = Field(default_factory=list)
    missing_links: list[LinkEdge] = Field(default_factory=list)


class PageUpsert(BaseModel):
    content: str = Field(min_length=1)


class SearchHit(BaseModel):
    page: PageSummary
    score: int
    matches: list[str] = Field(default_factory=list)
    observed_at: str | None = Field(default=None, description="When the fact was observed (capture time).")
    valid_from: str | None = Field(default=None, description="When the fact became true in the world.")
    valid_until: str | None = Field(default=None, description="When the fact ceased to be true.")
    actor: str | None = Field(default=None, description="Agent that created this content.")
    lane: str | None = Field(default=None, description="Retrieval lane (project, ops, research).")
    source_refs: list[str] = Field(
        default_factory=list, description="Source page/capture IDs referenced by this content."
    )


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class RagRetrieveRequest(BaseModel):
    query: str
    limit: int = 10
    expand_hops: int = Field(default=2, ge=0, le=4, description="Max graph expansion hops from initial hits.")
    expand_edge_types: list[str] = Field(
        default_factory=list, description="Edge types to follow during expansion. Empty = all."
    )
    include_claims: bool = Field(default=True, description="Include supporting/contradicting claims in results.")
    include_traces: bool = Field(default=False, description="Include reasoning trace references in results.")
    include_decisions: bool = Field(default=True, description="Include decision page references in results.")


class RagExpandRequest(BaseModel):
    """Request for multi-hop retrieval with graph expansion."""

    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    expand_hops: int = Field(default=2, ge=0, le=4, description="Max graph expansion hops from initial hits.")
    expand_edge_types: list[str] = Field(
        default_factory=list, description="Edge types to follow during expansion. Empty = all."
    )
    include_claims: bool = Field(default=True, description="Include supporting/contradicting claims in results.")
    include_traces: bool = Field(default=False, description="Include reasoning trace references in results.")
    include_decisions: bool = Field(default=True, description="Include decision page references in results.")


class RagRelevancePath(BaseModel):
    """A relevance path from a search hit through the context graph."""

    source_type: str = Field(description="Node type of the search hit.")
    source_id: str = Field(description="Page ID of the search hit.")
    path: list[dict[str, str]] = Field(
        default_factory=list, description="List of {edge_type, target_type, target_id} hops."
    )
    explanation: str = Field(description="Human-readable relevance explanation.")


class RagExpandedResult(BaseModel):
    """An enriched RAG result with graph expansion context."""

    page_id: str
    title: str | None = None
    score: float
    sources: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    relevance_paths: list[RagRelevancePath] = Field(
        default_factory=list, description="Paths from direct hits to this page through the context graph."
    )
    supporting_claims: list[str] = Field(default_factory=list, description="Claim IDs that support this page.")
    contradicting_claims: list[str] = Field(default_factory=list, description="Claim IDs that contradict this page.")
    related_decisions: list[str] = Field(default_factory=list, description="Decision page IDs related to this result.")
    related_traces: list[str] = Field(default_factory=list, description="Trace IDs that reference this page.")
    epistemic_status: str | None = None
    matched_entities: list[str] = Field(
        default_factory=list, description="Entity IDs/names reached during graph expansion."
    )
    relevant_because: str = Field(default="", description="Human-readable summary of why this result is relevant.")
    observed_at: str | None = Field(default=None)
    valid_from: str | None = Field(default=None)
    valid_until: str | None = Field(default=None)
    actor: str | None = Field(default=None)
    lane: str | None = Field(default=None)
    source_refs: list[str] = Field(default_factory=list)


class RagExpandedResponse(BaseModel):
    """Response for multi-hop retrieval with graph expansion."""

    query: str
    total: int
    results: list[RagExpandedResult] = Field(default_factory=list)
    expansion_stats: dict[str, int] = Field(
        default_factory=dict, description="Stats about graph expansion (hops, nodes_visited, paths_found)."
    )


class RagEvaluateRequest(BaseModel):
    query: str | None = None
    expected: list[str] = Field(default_factory=list)
    queries: list[dict[str, Any]] | None = None
    k: int = 10


class RagEvaluateResult(BaseModel):
    mean_precision: float
    mean_recall: float
    mean_f1: float
    per_query: list[dict[str, Any]] = Field(default_factory=list)


class CatalogResponse(BaseModel):
    kinds: list[str]
    visibilities: list[str]
    tags: list[str]


class ErrorResponse(BaseModel):
    error: Literal["not_found", "invalid_page_id"]
    message: str


class DailyDistillRequest(BaseModel):
    date: str | None = Field(default=None, description="ISO date (YYYY-MM-DD). Defaults to today.")
    actor: str | None = Field(default=None, description="Agent performing the distillation.")


class DailyDistillCapture(BaseModel):
    page_id: str
    title: str
    summary: str | None = None
    confidence: str | None = None
    lane: str | None = None


class DailyDistillResponse(BaseModel):
    date: str
    page_id: str
    capture_count: int
    captures: list[DailyDistillCapture] = Field(default_factory=list)
    content: str


class PendingDay(BaseModel):
    date: str
    capture_count: int


class PendingDaysResponse(BaseModel):
    pending_days: list[PendingDay] = Field(default_factory=list)
    total: int = 0


class HeartbeatCategory(BaseModel):
    """Count and items for a single review category."""

    count: int
    items: list[dict[str, Any]] = Field(default_factory=list)


class HeartbeatResponse(BaseModel):
    """Aggregated heartbeat review report."""

    generated_at: str
    total_issues: int
    stale_pages: HeartbeatCategory
    missing_metadata: HeartbeatCategory
    contradictions: HeartbeatCategory
    low_confidence: HeartbeatCategory
    expired_facts: HeartbeatCategory
    procedure_issues: HeartbeatCategory


class HeartbeatCaptureResponse(BaseModel):
    """Response for heartbeat self-audit captures."""

    captures: list[PageDetail] = Field(default_factory=list)
    categories_covered: list[str] = Field(default_factory=list)
    skipped_categories: list[str] = Field(
        default_factory=list,
        description="Categories skipped because captures already exist for today or no issues were found.",
    )


class MemoryHealthResponse(BaseModel):
    """Memory health surface: consolidation freshness and review signals."""

    pending_captures: int = 0
    review_required: int = 0
    rejected_plans: int = 0
    failed_runs: int = 0
    last_consolidation: str | None = None
    stale_pages: int = 0
    contradictions: int = 0
    low_confidence: int = 0
    expired_facts: int = 0
    missing_metadata: int = 0
    procedure_issues: int = 0
    total_issues: int = 0


class RepeatedCaptureGroup(BaseModel):
    """A group of captures with similar content suggesting a repeated procedure."""

    group_key: str
    captures: list[PageSummary] = Field(default_factory=list)
    count: int
    suggested_title: str
    suggested_trigger: str


class ProcedureCandidateProposal(BaseModel):
    """Request to create a procedure candidate from repeated captures."""

    capture_ids: list[str] = Field(min_length=2)
    title: str | None = None
    trigger: str | None = None
    lane: str | None = Field(default=None, description="Retrieval lane: procedural, ops, etc.")


class ProcedureCandidateResponse(BaseModel):
    """Response after creating a procedure candidate page."""

    page: PageDetail
    source_captures: list[PageSummary] = Field(default_factory=list)
    message: str


class ProcedureArtifact(BaseModel):
    """A formalized procedure artifact with strict metadata."""

    page_id: str
    title: str
    schema_version: str = "1.0"
    author: str = ""
    trigger: str
    steps: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    error_handling: str = ""
    validated: bool = False
    validated_at: str | None = None
    source_capture_ids: list[str] = Field(default_factory=list)
    epistemic_status: str | None = None


class ProcedureExportRequest(BaseModel):
    """Request to export a procedure page as a skill artifact."""

    page_id: str = Field(min_length=1)
    format: Literal["skill", "markdown"] = Field(default="skill", description="Export format.")


class ProcedureExportResponse(BaseModel):
    """Response for procedure export."""

    page_id: str
    format: str
    content: str
    filename: str


class ExtractedEntity(BaseModel):
    """An entity extracted from a capture."""

    name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None
    target_page_hint: str | None = None


class ExtractedClaim(BaseModel):
    """A factual claim extracted from a capture."""

    subject: str
    predicate: str
    object: str
    confidence: str
    actor: str | None = None
    lane: str | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_task: str | None = None
    decision_id: str | None = None
    trace_id: str | None = None
    policies_applied: list[str] = Field(default_factory=list)
    evidence: str | None = None
    section: str | None = None
    source_page_ids: list[str] = Field(default_factory=list)
    epistemic_status: EpistemicStatus | None = None
    model_version: str | None = None
    prompt_hash: str | None = None
    token_usage: dict[str, int] | None = None


class ExtractedEdge(BaseModel):
    """A relationship between entities."""

    source_entity: str
    relationship_type: str
    target_entity: str
    strength: float = 0.5
    evidence: str | None = None
    source_page_ids: list[str] = Field(default_factory=list)


class ExtractedInvalidation(BaseModel):
    """A claim that supersedes an older claim."""

    old_fact: str
    new_fact: str
    reason: str
    target_page_ids: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Result of extracting structured candidates from captures."""

    batch_id: str
    entities: list[ExtractedEntity] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)
    invalidations: list[ExtractedInvalidation] = Field(default_factory=list)
    source_capture_ids: list[str] = Field(default_factory=list)
    processed_at: str


class ExtractionRequest(BaseModel):
    """Request to run extraction on unprocessed captures."""

    capture_ids: list[str] | None = Field(
        default=None,
        description="Specific capture IDs. If None, process all unprocessed drafts.",
    )
    batch_size: int = Field(default=10, ge=1, le=50)
    dry_run: bool = Field(default=True, description="If True, return extraction results without storing in the ledger.")
    provider: str | None = Field(
        default=None,
        description=(
            "Extraction provider override. "
            "'none' or 'deterministic' for rule-based extraction, "
            "'escalation' for escalation-model-only extraction, "
            "or None for default LLM extraction."
        ),
    )


class ExtractionStatusResponse(BaseModel):
    """Status overview of the extraction pipeline."""

    total_draft_captures: int
    total_extracted: int
    total_pending: int
    last_batch_id: str | None = None
    last_run_at: str | None = None


class ExtractionResetRequest(BaseModel):
    """Request to reset extraction state for all or selected captures."""

    capture_ids: list[str] | None = Field(
        default=None,
        description="Specific capture IDs. If None, reset all extraction state.",
    )


class ExtractionResetResponse(BaseModel):
    """Result of resetting extraction state."""

    reset_count: int


class ClaimReinforcementResult(BaseModel):
    """Result of reinforcing or inserting a claim candidate."""

    candidate_id: str
    action: Literal["reinforced", "inserted"]
    previous_strength: float | None = None
    new_strength: float
    merged_source_capture_ids: list[str] = Field(default_factory=list)
    merged_source_page_ids: list[str] = Field(default_factory=list)


class ClaimSupersedeResult(BaseModel):
    """Result of superseding an old claim with a new one."""

    old_candidate_id: str
    new_candidate_id: str
    reason: str
    old_status: str


class DecayResult(BaseModel):
    """Result of applying decay to active claims."""

    decayed_count: int
    min_strength: float
    max_strength: float


class PatchOperation(StrEnum):
    insert_new_fact = "insert_new_fact"
    append_sourced_paragraph = "append_sourced_paragraph"
    update_existing_fact = "update_existing_fact"
    mark_stale = "mark_stale"
    create_stub_page = "create_stub_page"


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class PatchPlanStatus(StrEnum):
    ready = "ready"
    needs_manual_review = "needs_manual_review"
    pending = "pending"
    applied = "applied"
    rejected = "rejected"
    rolled_back = "rolled_back"


class PolicyRule(BaseModel):
    """A policy that gates patch planning decisions."""

    policy_id: str = Field(..., description="Unique policy identifier, e.g. 'protected-surface:v1'.")
    name: str = Field(..., min_length=1, max_length=200, description="Human-readable policy name.")
    description: str = Field(default="", max_length=2000, description="What this policy enforces.")
    gate: Literal[
        "auto-apply",
        "review-required",
        "protected-surface",
        "contradiction-review",
        "risk-assessment",
    ] = Field(..., description="Which decision gate this policy controls.")
    condition_kind: list[str] = Field(
        default_factory=list, description="Page kinds this policy applies to (empty = all)."
    )
    condition_operation: list[str] = Field(
        default_factory=list, description="Patch operations this policy applies to (empty = all)."
    )
    effect_pass: str = Field(default="allow", description="Result when policy passes: 'allow' or 'auto-apply'.")
    effect_fail: str = Field(default="block", description="Result when policy fails: 'block', 'review', or 'escalate'.")
    fail_reason_template: str = Field(
        default="", description="Template for the failure reason. {kind}, {operation}, {page_id} available."
    )
    version: int = Field(default=1, ge=1, description="Policy version for tracking changes.")
    enabled: bool = Field(default=True, description="Whether this policy is active.")

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9-]*:v\d+$", v):
            raise ValueError(f"policy_id must match 'name:vN' format (lowercase, colon, version number): {v!r}")
        return v

    @field_validator("effect_pass")
    @classmethod
    def validate_effect_pass(cls, v: str) -> str:
        if v not in {"allow", "auto-apply"}:
            raise ValueError(f"effect_pass must be 'allow' or 'auto-apply', got {v!r}")
        return v

    @field_validator("effect_fail")
    @classmethod
    def validate_effect_fail(cls, v: str) -> str:
        if v not in {"block", "review", "escalate"}:
            raise ValueError(f"effect_fail must be 'block', 'review', or 'escalate', got {v!r}")
        return v

    @field_validator("condition_operation")
    @classmethod
    def validate_condition_operations(cls, v: list[str]) -> list[str]:
        valid = {operation.value for operation in PatchOperation}
        invalid = [op for op in v if op not in valid]
        if invalid:
            raise ValueError(f"Invalid condition_operation value(s): {invalid}. Valid: {valid}")
        return v


class PolicyDecision(BaseModel):
    """Result of evaluating a single policy against a patch plan context."""

    policy_id: str
    gate: str
    passed: bool
    reason: str = ""


class PatchPlan(BaseModel):
    plan_id: str
    trace_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    target_page_id: str
    target_section: str | None = None
    operation: PatchOperation
    content_diff: str
    risk_level: RiskLevel
    auto_appliable: bool
    policies_applied: list[PolicyDecision] = Field(
        default_factory=list, description="Policy evaluation results for this plan."
    )
    status: PatchPlanStatus = PatchPlanStatus.pending
    created_at: str
    reason: str | None = None
    applied_at: str | None = None
    rejected_at: str | None = None
    rejection_reason: str | None = None


class PatchPreview(BaseModel):
    plan_id: str
    target_page_id: str
    operation: PatchOperation
    current_content: str
    proposed_content: str
    unified_diff: str
    risk_level: RiskLevel
    auto_appliable: bool
    policies_applied: list[PolicyDecision] = Field(
        default_factory=list, description="Policy evaluation results for this plan."
    )


class PatchApplyResult(BaseModel):
    plan_id: str
    target_page_id: str
    operation: PatchOperation
    before_hash: str
    after_hash: str
    applied_at: str
    auto_applied: bool


class ConsolidationRunRequest(BaseModel):
    """Request to run the full consolidation worker pipeline."""

    dry_run: bool = True
    batch_size: int = Field(default=10, ge=1, le=50)
    max_auto_apply: int = Field(default=0, ge=0, le=100)
    force_reextract: bool = False


class ConsolidationRunResult(BaseModel):
    """Result of a consolidation worker run."""

    batch_id: str
    captures_processed: int
    candidates_extracted: int
    plans_generated: int
    auto_applied: int
    review_required: int
    errors: list[str] = Field(default_factory=list)
    dry_run: bool
    blocked_claims: list[dict[str, str]] = Field(
        default_factory=list, description="Claims blocked from auto-apply with reason."
    )


class RollbackResult(BaseModel):
    """Result of rolling back an applied patch plan."""

    plan_id: str
    page_id: str
    before_hash: str
    after_hash: str
    rolled_back_at: str


class ConsolidationPlanRequest(BaseModel):
    batch_id: str | None = None
    candidate_ids: list[str] | None = None


class PatchApplyRequest(BaseModel):
    force: bool = False


class PatchRejectRequest(BaseModel):
    reason: str | None = None


class LedgerReinforceRequest(BaseModel):
    """Request to reinforce or insert a claim candidate."""

    subject: str
    predicate: str
    object: str
    confidence: str = "unknown"
    actor: str | None = None
    lane: str | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    evidence: str | None = None
    source_page_ids: list[str] = Field(default_factory=list)


class LedgerSupersedeRequest(BaseModel):
    """Request to supersede an old claim with a new one."""

    old_candidate_id: str
    new_candidate_id: str
    reason: str


class LedgerClaimQuery(BaseModel):
    """Query parameters for active claims."""

    subject: str | None = None
    lane: str | None = None
    min_strength: float = 0.0
    valid_at: str | None = None


class ExtractedCandidateResponse(BaseModel):
    """A typed extraction candidate with full provenance fields."""

    candidate_id: str
    batch_id: str
    candidate_type: str
    status: str
    confidence: str | None = None
    epistemic_status: str | None = None
    actor: str | None = None
    lane: str | None = None
    observed_at: str | None = Field(default=None, description="When the fact was observed (capture time).")
    valid_from: str | None = Field(default=None, description="When the fact became true in the world.")
    valid_until: str | None = Field(default=None, description="When the fact ceased to be true (null = still valid).")
    strength: float = 0.5
    source_capture_ids: list[str] = Field(
        default_factory=list, description="Capture IDs that contributed to this candidate."
    )
    source_page_ids: list[str] = Field(
        default_factory=list, description="Lore page IDs that contributed to this candidate."
    )
    evidence: str | None = Field(default=None, description="Supporting evidence text extracted from the claim content.")
    model_version: str | None = None
    prompt_hash: str | None = None
    token_usage: dict[str, int] | None = None
    content: dict[str, Any] | None = Field(default=None, description="The full parsed content JSON.")
    created_at: str
    updated_at: str
