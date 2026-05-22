from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PageSummary(BaseModel):
    id: str
    title: str
    kind: str
    visibility: str
    status: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    updated_at: str
    size: int


class PageDetail(PageSummary):
    content: str
    body: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class LoreApiKeyRole(str, Enum):
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


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class RagRetrieveRequest(BaseModel):
    query: str
    limit: int = 10


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
    evidence: str | None = None
    source_page_ids: list[str] = Field(default_factory=list)


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
    provider: str | None = Field(default=None, description="LLM provider override. Defaults to configured provider.")


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


class PatchOperation(str, Enum):
    insert_new_fact = "insert_new_fact"
    append_sourced_paragraph = "append_sourced_paragraph"
    update_existing_fact = "update_existing_fact"
    mark_stale = "mark_stale"
    create_stub_page = "create_stub_page"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class PatchPlan(BaseModel):
    plan_id: str
    candidate_ids: list[str] = Field(default_factory=list)
    target_page_id: str
    target_section: str | None = None
    operation: PatchOperation
    content_diff: str
    risk_level: RiskLevel
    auto_appliable: bool
    status: str = "pending"
    created_at: str
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
