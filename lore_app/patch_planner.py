from __future__ import annotations

import difflib
import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .audit import AuditLog, new_audit_entry
from .capture import is_capture_page_id, slugify
from .frontmatter import serialize_markdown, update_frontmatter
from .ledger import LedgerDB, utc_now
from .repository import InvalidPageId, LoreRepository, infer_kind, normalize_page_id, optional_string
from .route_utils import index_vectors_for_page
from .schemas import (
    Alternative,
    ContextRef,
    ExtractedClaim,
    PatchApplyResult,
    PatchOperation,
    PatchPlan,
    PatchPlanStatus,
    PatchPreview,
    PolicyDecision,
    RiskLevel,
    ToolRef,
    TraceEntry,
)

if TYPE_CHECKING:
    from .link_graph import LinkGraphCache
    from .policy_engine import PolicyEngine
    from .rag.vector_store import VectorStore
    from .search_index import LoreSearchIndex


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _CandidateBundle:
    row: dict[str, Any]
    claim: dict[str, Any]


@dataclass(frozen=True)
class _RenderedPatch:
    content: str
    status: PatchPlanStatus = PatchPlanStatus.ready
    reason: str | None = None


class PatchPlanner:
    def __init__(
        self,
        repo: LoreRepository,
        ledger: LedgerDB,
        audit_log: AuditLog | None = None,
        *,
        search_index: LoreSearchIndex | None = None,
        vector_store: VectorStore | None = None,
        graph_cache: LinkGraphCache | None = None,
        policy_engine: PolicyEngine | None = None,
    ):
        self.repo = repo
        self.ledger = ledger
        self.audit_log = audit_log
        self.search_index = search_index
        self.vector_store = vector_store
        self.graph_cache = graph_cache
        self.policy_engine = policy_engine

    def plan_batch(
        self,
        batch_id: str | None = None,
        candidate_ids: list[str] | None = None,
    ) -> list[PatchPlan]:
        candidates = self._collect_candidates(batch_id=batch_id, candidate_ids=candidate_ids)
        grouped: dict[str, list[_CandidateBundle]] = defaultdict(list)

        for candidate in candidates:
            target_page_id = self._target_page_id(candidate)
            if target_page_id is None:
                continue
            grouped[target_page_id].append(candidate)

        plans: list[PatchPlan] = []
        for target_page_id, bundles in grouped.items():
            plan = self._build_plan(target_page_id, bundles)
            self.ledger.store_patch_plan(plan, batch_id=batch_id or self._batch_id_for_group(bundles))
            plans.append(plan)
        return plans

    def preview_patch(self, plan_id: str) -> PatchPreview:
        plan = self._load_plan(plan_id)
        current_content = self._current_content(plan.target_page_id)
        proposed_content = self._proposed_content(plan)
        return PatchPreview(
            plan_id=plan.plan_id,
            target_page_id=plan.target_page_id,
            operation=plan.operation,
            current_content=current_content,
            proposed_content=proposed_content,
            unified_diff=_unified_diff(current_content, proposed_content, plan.target_page_id),
            risk_level=plan.risk_level,
            auto_appliable=plan.auto_appliable,
            policies_applied=plan.policies_applied,
        )

    def apply_plan(self, plan_id: str, *, force: bool = False) -> PatchApplyResult:
        plan = self._load_plan(plan_id)
        # Terminal-state guard: re-applying an applied/rejected/rolled-back plan
        # would re-mutate the page and then crash on an invalid candidate
        # transition (active -> active), leaving a partial, non-idempotent state.
        if plan.status in (PatchPlanStatus.applied, PatchPlanStatus.rejected, PatchPlanStatus.rolled_back):
            raise ValueError(f"Patch plan {plan_id} cannot be applied from status '{plan.status}'.")
        if not plan.auto_appliable and not force:
            raise ValueError(f"Patch plan {plan_id} is not auto-appliable; pass force=True to apply it.")

        # Pre-flight the candidate activations BEFORE mutating the page or plan row.
        # Two distinct plans can share a candidate (plan_batch with explicit
        # candidate_ids skips the open-plan dedup). If another such plan was applied
        # first, this candidate is already 'active'; _activate_candidates would then
        # raise 'active -> active' AFTER the page was overwritten and the plan flipped
        # to 'applied', leaving a partial, non-idempotent write reported as a 409.
        # Failing here keeps the 409 actionable with no side effects.
        statuses = self.ledger.candidate_statuses(plan.candidate_ids)
        already_active = [cid for cid in plan.candidate_ids if statuses.get(cid) == "active"]
        if already_active:
            raise ValueError(
                f"Patch plan {plan_id} cannot be applied: candidate(s) {', '.join(already_active)} "
                "already active (applied by another plan)."
            )

        preview = self.preview_patch(plan_id)
        before_hash = _content_hash(preview.current_content)
        after_hash = _content_hash(preview.proposed_content)
        applied_at = utc_now()

        self.repo.upsert_page(plan.target_page_id, preview.proposed_content)
        self.ledger.update_plan_status(plan_id, "applied", applied_at=applied_at, content_diff=preview.unified_diff)
        self._accept_source_captures(plan.candidate_ids)
        self._activate_candidates(plan.candidate_ids)
        self._reindex_page(plan.target_page_id)
        self._record_audit(plan, before_hash, after_hash, preview.unified_diff, before_content=preview.current_content)
        self._complete_plan_trace(plan, f"Applied {plan.operation} to {plan.target_page_id}")

        return PatchApplyResult(
            plan_id=plan.plan_id,
            target_page_id=plan.target_page_id,
            operation=plan.operation,
            before_hash=before_hash,
            after_hash=after_hash,
            applied_at=applied_at,
            auto_applied=plan.auto_appliable and not force,
        )

    def reject_plan(self, plan_id: str, reason: str | None = None) -> None:
        plan = self._load_plan(plan_id)
        # Only open plans can be rejected. Rejecting an applied plan would try an
        # invalid active -> rejected candidate transition after the plan row was
        # already flipped, corrupting state.
        if plan.status not in (
            PatchPlanStatus.pending,
            PatchPlanStatus.ready,
            PatchPlanStatus.needs_manual_review,
        ):
            raise ValueError(f"Patch plan {plan_id} cannot be rejected from status '{plan.status}'.")
        rejected_at = utc_now()
        # Reject the candidates first so a transition failure cannot leave the
        # plan flipped to 'rejected' with still-open candidates.
        for candidate_id in plan.candidate_ids:
            self.ledger.reject_candidate(candidate_id, reason=reason or "Patch plan rejected")
        self.ledger.update_plan_status(
            plan_id,
            "rejected",
            rejected_at=rejected_at,
            rejection_reason=reason,
        )
        self._abandon_plan_trace(plan, f"Rejected: {reason or 'no reason given'}")

    def _collect_candidates(
        self,
        *,
        batch_id: str | None,
        candidate_ids: list[str] | None,
    ) -> list[_CandidateBundle]:
        clauses = ["candidate_type = 'claim'", "status = 'candidate'"]
        params: list[Any] = []
        if batch_id:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        if candidate_ids:
            placeholders = ", ".join("?" for _ in candidate_ids)
            clauses.append(f"candidate_id IN ({placeholders})")
            params.extend(candidate_ids)
        else:
            # Bulk planning is idempotent: skip candidates that already have an open
            # (non-terminal) patch plan so repeated plan_batch() calls do not pile up
            # duplicate plans for the same claim.
            clauses.append(
                "candidate_id NOT IN ("
                "SELECT je.value FROM patch_plans p, json_each(p.candidate_ids) je "
                "WHERE p.status IN ('pending', 'ready', 'needs_manual_review'))"
            )
        rows = self.ledger.connection.execute(
            f"""
            SELECT *
            FROM extraction_candidates
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at ASC, candidate_id
            """,
            params,
        ).fetchall()
        bundles: list[_CandidateBundle] = []
        for row in rows:
            decoded = self._decode_candidate_row(dict(row))
            claim = decoded["content_json"]
            bundles.append(_CandidateBundle(row=decoded, claim=claim))
        return bundles

    def _build_plan(self, target_page_id: str, bundles: list[_CandidateBundle]) -> PatchPlan:
        now = utc_now()
        page = self.repo.read_page(target_page_id)
        contradictions = self._find_contradictions(bundles)
        operation = self._choose_operation(target_page_id, page is not None, bundles, contradictions)
        risk_level = self._assess_risk(target_page_id, operation, contradictions)
        high_confidence = all(optional_string(bundle.row.get("confidence")) == "high" for bundle in bundles)
        epistemic_status = _weakest_epistemic_status(bundles)
        bundle_trace_id = _any_trace_id(bundles)
        auto_appliable = self._is_auto_appliable(target_page_id, operation, bundles, contradictions)
        policy_decisions = self._evaluate_policies(
            target_page_id,
            operation,
            bool(contradictions),
            high_confidence,
            epistemic_status=epistemic_status,
            trace_id=bundle_trace_id,
        )
        if any(not decision.passed for decision in policy_decisions):
            auto_appliable = False
        risk_level = self._risk_level_from_policy_decisions(risk_level, policy_decisions)
        target_section = self._target_section(target_page_id, operation)
        candidate_ids = [bundle.row["candidate_id"] for bundle in bundles]
        trace = self.ledger.store_trace(
            TraceEntry(
                trace_id="",
                actor="patch-planner",
                reason_summary=(
                    f"Generated {operation.value} plan for {target_page_id}: "
                    f"{risk_level.value} risk, auto_appliable={auto_appliable}"
                ),
                context_refs=[ContextRef(type="candidate", id=str(candidate_id)) for candidate_id in candidate_ids],
                tool_refs=[ToolRef(tool="patch-planner", action="build_plan")],
                constraints=self._trace_constraints(target_page_id, contradictions),
                policy_refs=self._trace_policy_refs(target_page_id, operation, auto_appliable, policy_decisions),
                alternatives=self._trace_alternatives(auto_appliable),
                outcome="plan_generated",
                related_ids={
                    "page_id": target_page_id,
                    "candidate_ids": ",".join(str(candidate_id) for candidate_id in candidate_ids),
                },
            )
        )

        rendered = self._render_content_result(
            target_page_id,
            operation,
            target_section,
            bundles,
            page_content=page.content if page else "",
        )
        if rendered.status == PatchPlanStatus.needs_manual_review:
            auto_appliable = False
        preview = self._preview_for(
            target_page_id,
            operation,
            target_section,
            bundles,
            rendered=rendered,
            page_content=page.content if page else "",
            policies_applied=policy_decisions,
        )
        plan = PatchPlan(
            plan_id=str(uuid.uuid4()),
            trace_id=trace.trace_id,
            candidate_ids=candidate_ids,
            target_page_id=target_page_id,
            target_section=target_section,
            operation=operation,
            content_diff=preview.unified_diff,
            risk_level=risk_level,
            auto_appliable=auto_appliable,
            policies_applied=policy_decisions,
            status=(
                PatchPlanStatus.needs_manual_review
                if rendered.status == PatchPlanStatus.needs_manual_review
                else PatchPlanStatus.pending
            ),
            created_at=now,
            reason=rendered.reason,
        )
        return plan

    def _complete_plan_trace(self, plan: PatchPlan, outcome: str) -> None:
        if not plan.trace_id:
            return
        trace = self.ledger.get_trace(plan.trace_id)
        if trace is None:
            return
        self.ledger.store_trace(
            trace.model_copy(update={"status": "completed", "outcome": outcome, "updated_at": utc_now()})
        )

    def _abandon_plan_trace(self, plan: PatchPlan, outcome: str) -> None:
        if not plan.trace_id:
            return
        trace = self.ledger.get_trace(plan.trace_id)
        if trace is None:
            return
        self.ledger.store_trace(
            trace.model_copy(update={"status": "abandoned", "outcome": outcome, "updated_at": utc_now()})
        )

    def _trace_constraints(
        self,
        target_page_id: str,
        contradictions: list[dict[str, Any]],
    ) -> list[str]:
        constraints: list[str] = []
        if contradictions:
            constraints.append(f"Detected {len(contradictions)} contradicting candidate(s).")
        kind = self._page_kind(target_page_id)
        if kind in {"decision", "runbook"}:
            constraints.append(f"Protected {kind} pages require review before auto-apply.")
        return constraints

    def _evaluate_policies(
        self,
        target_page_id: str,
        operation: PatchOperation,
        has_contradictions: bool,
        high_confidence: bool,
        *,
        epistemic_status: str | None = None,
        trace_id: str | None = None,
    ) -> list[PolicyDecision]:
        if self.policy_engine is None:
            return []
        return self.policy_engine.evaluate(
            page_id=target_page_id,
            page_kind=self._page_kind(target_page_id),
            operation=operation,
            has_contradictions=has_contradictions,
            high_confidence=high_confidence,
            epistemic_status=epistemic_status,
            trace_id=trace_id,
        )

    def _trace_policy_refs(
        self,
        target_page_id: str,
        operation: PatchOperation,
        auto_appliable: bool,
        policy_decisions: list[PolicyDecision] | None = None,
    ) -> list[str]:
        if policy_decisions:
            return [f"{decision.policy_id}:{'pass' if decision.passed else 'fail'}" for decision in policy_decisions]
        policy_refs = ["patch-planner:auto-apply"]
        if not auto_appliable:
            policy_refs.append("patch-planner:review-required")
        if self._page_kind(target_page_id) in {"decision", "runbook"}:
            policy_refs.append("patch-planner:protected-surface")
        if operation in {PatchOperation.update_existing_fact, PatchOperation.mark_stale}:
            policy_refs.append("patch-planner:contradiction-review")
        return policy_refs

    def _trace_alternatives(self, auto_appliable: bool) -> list[Alternative]:
        if auto_appliable:
            return [
                Alternative(
                    description="Mark as review-only",
                    rejected_reason="Plan is auto-appliable",
                )
            ]
        return [
            Alternative(
                description="Auto-apply the plan",
                rejected_reason="Plan requires review",
            )
        ]

    def _risk_level_from_policy_decisions(
        self,
        fallback: RiskLevel,
        policy_decisions: list[PolicyDecision],
    ) -> RiskLevel:
        if any(decision.gate == "risk-assessment" and not decision.passed for decision in policy_decisions):
            return RiskLevel.high
        return fallback

    def _load_plan(self, plan_id: str) -> PatchPlan:
        row = self.ledger.get_patch_plan(plan_id)
        if row is None:
            raise ValueError(f"Patch plan {plan_id} not found")
        return PatchPlan.model_validate(row)

    def _target_page_id(self, candidate: _CandidateBundle) -> str | None:
        source_page_ids = [normalize_page_id(page_id) for page_id in candidate.row["source_page_ids"]]
        non_capture_source_page_ids = [page_id for page_id in source_page_ids if not is_capture_page_id(page_id)]
        if non_capture_source_page_ids:
            return non_capture_source_page_ids[0]

        capture_target = self._suggested_target_page(self._candidate_capture_ids(candidate))
        if capture_target:
            return capture_target

        subject = optional_string(candidate.claim.get("subject"))
        if subject and "/" in subject:
            try:
                target = normalize_page_id(subject)
                if not is_capture_page_id(target):
                    return target
            except InvalidPageId:
                return None

        # No route could be derived. Rather than strand the claim forever, give it a
        # deterministic durable home: a per-actor memory page (non-capture namespace,
        # so it is not re-extracted). This guarantees every capture consolidates.
        actor_slug = slugify(optional_string(candidate.row.get("actor")) or "shared") or "shared"
        try:
            return normalize_page_id(f"memory/{actor_slug}")
        except InvalidPageId:  # pragma: no cover - actor_slug is already sanitized
            return None

    def _batch_id_for_group(self, bundles: list[_CandidateBundle]) -> str | None:
        batch_ids = {optional_string(bundle.row.get("batch_id")) for bundle in bundles}
        batch_ids.discard(None)
        return next(iter(batch_ids), None)

    def _find_contradictions(self, bundles: list[_CandidateBundle]) -> list[dict[str, Any]]:
        contradictions: list[dict[str, Any]] = []
        for bundle in bundles:
            for row in self.ledger.find_contradicting_claims(self._extracted_claim(bundle.claim)):
                if row["candidate_id"] != bundle.row["candidate_id"]:
                    contradictions.append(row)
        return contradictions

    def _choose_operation(
        self,
        target_page_id: str,
        page_exists: bool,
        bundles: list[_CandidateBundle],
        contradictions: list[dict[str, Any]],
    ) -> PatchOperation:
        if not page_exists and self._stub_allowed(target_page_id, bundles):
            return PatchOperation.create_stub_page
        if contradictions:
            current_content = self._current_content(target_page_id)
            if any(
                optional_string(contradiction.get("content_json", {}).get("object")) in current_content
                for contradiction in contradictions
            ):
                return PatchOperation.update_existing_fact
            return PatchOperation.mark_stale
        if self._target_section(target_page_id, PatchOperation.append_sourced_paragraph) in self._current_content(
            target_page_id
        ):
            return PatchOperation.append_sourced_paragraph
        return PatchOperation.insert_new_fact

    def _assess_risk(
        self,
        target_page_id: str,
        operation: PatchOperation,
        contradictions: list[dict[str, Any]],
    ) -> RiskLevel:
        kind = self._page_kind(target_page_id)
        if operation in {PatchOperation.update_existing_fact, PatchOperation.mark_stale}:
            return RiskLevel.high
        if kind in {"decision", "runbook"}:
            return RiskLevel.high if contradictions else RiskLevel.medium
        if kind in {"procedure", "service", "project", "ops"}:
            return RiskLevel.low if not contradictions else RiskLevel.medium
        return RiskLevel.medium

    def _is_auto_appliable(
        self,
        target_page_id: str,
        operation: PatchOperation,
        bundles: list[_CandidateBundle],
        contradictions: list[dict[str, Any]],
    ) -> bool:
        kind = self._page_kind(target_page_id)
        high_confidence = all(optional_string(bundle.row.get("confidence")) == "high" for bundle in bundles)
        if operation == PatchOperation.create_stub_page:
            return high_confidence and kind != "decision" and self._has_clear_stub_metadata(bundles)
        if operation in {PatchOperation.insert_new_fact, PatchOperation.append_sourced_paragraph}:
            return high_confidence and not contradictions and kind not in {"decision", "runbook"}
        return False

    def _target_section(self, target_page_id: str, operation: PatchOperation) -> str | None:
        if operation == PatchOperation.mark_stale:
            return "## Review Notes"
        kind = self._page_kind(target_page_id)
        if kind == "runbook":
            return "## Notes"
        return "## Facts"

    def _preview_for(
        self,
        target_page_id: str,
        operation: PatchOperation,
        target_section: str | None,
        bundles: list[_CandidateBundle],
        *,
        rendered: _RenderedPatch | None = None,
        page_content: str,
        policies_applied: list[PolicyDecision] | None = None,
    ) -> PatchPreview:
        if rendered is None:
            rendered = self._render_content_result(
                target_page_id,
                operation,
                target_section,
                bundles,
                page_content=page_content,
            )
        return PatchPreview(
            plan_id="preview",
            target_page_id=target_page_id,
            operation=operation,
            current_content=page_content,
            proposed_content=rendered.content,
            unified_diff=_unified_diff(page_content, rendered.content, target_page_id),
            risk_level=self._assess_risk(target_page_id, operation, self._find_contradictions(bundles)),
            auto_appliable=(
                rendered.status == PatchPlanStatus.ready
                and self._is_auto_appliable(target_page_id, operation, bundles, self._find_contradictions(bundles))
            ),
            policies_applied=policies_applied or [],
        )

    def _proposed_content(self, plan: PatchPlan) -> str:
        bundles = self._bundles_for_candidate_ids(plan.candidate_ids)
        current_content = self._current_content(plan.target_page_id)
        return self._render_content_result(
            plan.target_page_id,
            plan.operation,
            plan.target_section,
            bundles,
            page_content=current_content,
        ).content

    def _render_content_result(
        self,
        target_page_id: str,
        operation: PatchOperation,
        target_section: str | None,
        bundles: list[_CandidateBundle],
        *,
        page_content: str,
    ) -> _RenderedPatch:
        if operation == PatchOperation.create_stub_page:
            return _RenderedPatch(content=self._build_stub_page(target_page_id, bundles))

        detail = self.repo.read_page(target_page_id)
        content = self._build_stub_page(target_page_id, bundles) if detail is None else detail.content

        paragraphs = [self._candidate_paragraph(bundle) for bundle in bundles]
        if operation in {PatchOperation.insert_new_fact, PatchOperation.append_sourced_paragraph}:
            return _RenderedPatch(
                content=_insert_into_section(content, target_section or "## Facts", "\n\n".join(paragraphs))
            )
        if operation == PatchOperation.update_existing_fact:
            return self._render_update_existing_fact(content, bundles)
        if operation == PatchOperation.mark_stale:
            stale_marker = "<!-- stale: auto-consolidation detected a contradiction requiring review -->"
            return _RenderedPatch(
                content=_insert_into_section(content, target_section or "## Review Notes", stale_marker)
            )
        return _RenderedPatch(content=content)

    def _render_update_existing_fact(self, content: str, bundles: list[_CandidateBundle]) -> _RenderedPatch:
        new_content = content
        for bundle in bundles:
            replacement = self._candidate_paragraph(bundle)
            section_title = self._candidate_section_title(bundle)
            for contradiction in self.ledger.find_contradicting_claims(self._extracted_claim(bundle.claim)):
                old_object = optional_string(contradiction.get("content_json", {}).get("object"))
                if not old_object:
                    continue
                replacement_result = _replace_old_object_in_section(
                    new_content,
                    old_object,
                    replacement,
                    section_title=section_title,
                )
                if replacement_result.status == PatchPlanStatus.needs_manual_review:
                    return _RenderedPatch(
                        content=content,
                        status=PatchPlanStatus.needs_manual_review,
                        reason=replacement_result.reason,
                    )
                new_content = replacement_result.content
        return _RenderedPatch(content=new_content)

    def _candidate_section_title(self, bundle: _CandidateBundle) -> str | None:
        return optional_string(bundle.claim.get("section")) or optional_string(bundle.claim.get("anchor"))

    def _build_stub_page(self, target_page_id: str, bundles: list[_CandidateBundle]) -> str:
        first_capture = self._first_capture_detail(bundles)
        frontmatter = {
            "title": self._page_title(target_page_id, bundles),
            "kind": self._page_kind(target_page_id),
            "visibility": optional_string(first_capture.frontmatter.get("visibility")) if first_capture else "internal",
            "status": "draft",
            "summary": self._claim_summary(bundles[0]),
            "sources": self._all_capture_ids(bundles),
        }
        if not frontmatter["visibility"]:
            frontmatter["visibility"] = "internal"
        body = [
            f"# {frontmatter['title']}",
            "",
            "## Summary",
            "",
            self._claim_summary(bundles[0]),
            "",
            "## Facts",
            "",
            "\n\n".join(self._candidate_paragraph(bundle) for bundle in bundles),
        ]
        return serialize_markdown(frontmatter, "\n".join(body))

    def _candidate_paragraph(self, bundle: _CandidateBundle) -> str:
        claim = bundle.claim
        sources = ", ".join(f"[[{capture_id}]]" for capture_id in self._candidate_capture_ids(bundle))
        observed_at = optional_string(claim.get("observed_at"))
        statement = f"{claim['subject']} {claim['predicate']} {claim['object']}."
        if observed_at:
            statement += f" Observed at {observed_at}."
        return f"{statement} Source: {sources}."

    def _page_kind(self, target_page_id: str) -> str:
        existing = self.repo.read_page(target_page_id)
        if existing is not None:
            return optional_string(existing.frontmatter.get("kind")) or infer_kind(target_page_id)
        return infer_kind(target_page_id)

    def _page_title(self, target_page_id: str, bundles: list[_CandidateBundle]) -> str:
        existing = self.repo.read_page(target_page_id)
        if existing is not None:
            return existing.title
        claim_subject = optional_string(bundles[0].claim.get("subject"))
        if claim_subject == target_page_id:
            return target_page_id.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
        first_capture = self._first_capture_detail(bundles)
        return (
            optional_string(first_capture.title if first_capture else None)
            or target_page_id.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
        )

    def _claim_summary(self, bundle: _CandidateBundle) -> str:
        return optional_string(bundle.claim.get("object")) or "Auto-generated stub page."

    def _stub_allowed(self, target_page_id: str, bundles: list[_CandidateBundle]) -> bool:
        if self.repo.read_page(target_page_id) is not None:
            return False
        if self._page_kind(target_page_id) == "decision":
            return False
        return self._has_clear_stub_metadata(bundles)

    def _has_clear_stub_metadata(self, bundles: list[_CandidateBundle]) -> bool:
        capture = self._first_capture_detail(bundles)
        if capture is None:
            return False
        return all(
            [
                optional_string(capture.frontmatter.get("suggested_target_page")),
                optional_string(capture.frontmatter.get("summary")) or self._claim_summary(bundles[0]),
                optional_string(capture.frontmatter.get("visibility")) or "internal",
            ]
        )

    def _suggested_target_page(self, capture_ids: list[str]) -> str | None:
        for capture_id in capture_ids:
            capture = self.repo.read_page(capture_id)
            if capture is None:
                continue
            suggested = optional_string(capture.frontmatter.get("suggested_target_page"))
            if suggested:
                target = normalize_page_id(suggested)
                if not is_capture_page_id(target):
                    return target
        return None

    def _first_capture_detail(self, bundles: list[_CandidateBundle]):
        for capture_id in self._all_capture_ids(bundles):
            capture = self.repo.read_page(capture_id)
            if capture is not None:
                return capture
        return None

    def _all_capture_ids(self, bundles: list[_CandidateBundle]) -> list[str]:
        capture_ids: list[str] = []
        for bundle in bundles:
            for capture_id in self._candidate_capture_ids(bundle):
                if capture_id not in capture_ids:
                    capture_ids.append(capture_id)
        return capture_ids

    def _candidate_capture_ids(self, bundle: _CandidateBundle) -> list[str]:
        source_capture_ids = [
            normalize_page_id(page_id)
            for page_id in bundle.row["source_page_ids"]
            if is_capture_page_id(normalize_page_id(page_id))
        ]
        if source_capture_ids:
            return list(dict.fromkeys(source_capture_ids))
        return [
            normalize_page_id(capture_id)
            for capture_id in bundle.row["source_capture_ids"]
            if is_capture_page_id(normalize_page_id(capture_id))
        ]

    def _bundles_for_candidate_ids(self, candidate_ids: list[str]) -> list[_CandidateBundle]:
        if not candidate_ids:
            return []
        placeholders = ", ".join("?" for _ in candidate_ids)
        rows = self.ledger.connection.execute(
            f"SELECT * FROM extraction_candidates WHERE candidate_id IN ({placeholders}) ORDER BY created_at ASC, candidate_id",
            candidate_ids,
        ).fetchall()
        return [
            _CandidateBundle(row=decoded, claim=decoded["content_json"])
            for row in rows
            for decoded in [self._decode_candidate_row(dict(row))]
        ]

    def _decode_candidate_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for key in ("content_json", "source_capture_ids", "source_page_ids"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
        return row

    def _current_content(self, page_id: str) -> str:
        page = self.repo.read_page(page_id)
        return page.content if page is not None else ""

    def _extracted_claim(self, payload: dict[str, Any]) -> ExtractedClaim:
        return ExtractedClaim.model_validate(payload)

    def _activate_candidates(self, candidate_ids: list[str]) -> None:
        for candidate_id in candidate_ids:
            self.ledger.activate_candidate(candidate_id)

    def _reindex_page(self, page_id: str) -> None:
        """Update search index, vector store, and graph cache after a page change."""
        page = self.repo.read_page(page_id)
        if page is not None and self.search_index is not None:
            self.search_index.upsert_page_from_detail(page)
        if page is not None and self.vector_store is not None:
            index_vectors_for_page(self.vector_store, page)
        if self.graph_cache is not None:
            self.graph_cache.invalidate()

    def _accept_source_captures(self, candidate_ids: list[str]) -> None:
        for bundle in self._bundles_for_candidate_ids(candidate_ids):
            for capture_id in self._candidate_capture_ids(bundle):
                capture = self.repo.read_page(capture_id)
                if capture is None or capture.frontmatter.get("kind") != "capture":
                    continue
                if capture.frontmatter.get("status") == "accepted":
                    continue
                updated = update_frontmatter(capture.content, {"status": "accepted"})
                self.repo.upsert_page(capture_id, updated)
                self._reindex_page(capture_id)

    def _record_audit(
        self,
        plan: PatchPlan,
        before_hash: str,
        after_hash: str,
        unified_diff: str,
        *,
        before_content: str | None = None,
    ) -> None:
        if self.audit_log is None:
            return
        payload: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "candidate_ids": plan.candidate_ids,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "auto_applied": plan.auto_appliable,
        }
        if before_content is not None:
            payload["before_content"] = before_content
        summary = json.dumps(payload, sort_keys=True)
        self.audit_log.record(
            new_audit_entry(
                actor="patch_planner",
                operation="consolidation.apply",
                page_id=plan.target_page_id,
                summary=summary,
                diff_size=len(unified_diff),
            )
        )


def _unified_diff(current_content: str, proposed_content: str, page_id: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            current_content.splitlines(),
            proposed_content.splitlines(),
            fromfile=f"{page_id}.before",
            tofile=f"{page_id}.after",
            lineterm="",
        )
    )


_EPISTEMIC_ORDER = {"operator_declared": 0, "retrieved": 1, "inferred": 2, "assumption": 3}


def _weakest_epistemic_status(bundles: list[_CandidateBundle]) -> str | None:
    worst_rank = -1
    worst_status = None
    for bundle in bundles:
        status = bundle.claim.get("epistemic_status") or bundle.row.get("epistemic_status")
        if status and isinstance(status, str):
            rank = _EPISTEMIC_ORDER.get(status, -1)
            if rank > worst_rank:
                worst_rank = rank
                worst_status = status
    return worst_status


def _any_trace_id(bundles: list[_CandidateBundle]) -> str | None:
    for bundle in bundles:
        trace_id = bundle.claim.get("trace_id") or bundle.row.get("trace_id")
        if trace_id:
            return str(trace_id)
    return None


def _insert_into_section(content: str, heading: str, block: str) -> str:
    normalized_block = _dedupe_block_for_section(content, heading, block)
    if not normalized_block:
        return content
    if not content:
        return f"{heading}\n\n{normalized_block}\n"
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == heading:
            insert_at = index + 1
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            updated = [*lines[:insert_at], normalized_block, "", *lines[insert_at:]]
            return "\n".join(updated).rstrip() + "\n"
    if not content.endswith("\n"):
        content += "\n"
    return content.rstrip() + f"\n\n{heading}\n\n{normalized_block}\n"


def _dedupe_block_for_section(content: str, heading: str, block: str) -> str:
    incoming_paragraphs = _split_paragraphs(block)
    if not incoming_paragraphs:
        return ""
    existing_facts = {
        fact
        for paragraph in _section_paragraphs(content, heading)
        for fact in [_normalized_fact_text(paragraph)]
        if fact
    }
    kept: list[str] = []
    for paragraph in incoming_paragraphs:
        fact = _normalized_fact_text(paragraph)
        if fact and fact in existing_facts:
            continue
        kept.append(paragraph)
        if fact:
            existing_facts.add(fact)
    return "\n\n".join(kept)


def _split_paragraphs(block: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in block.strip().splitlines():
        if line.strip():
            current.append(line)
            continue
        if current:
            paragraphs.append("\n".join(current).strip())
            current = []
    if current:
        paragraphs.append("\n".join(current).strip())
    return paragraphs


def _section_paragraphs(content: str, heading: str) -> list[str]:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != heading:
            continue
        body_start = index + 1
        while body_start < len(lines) and lines[body_start].strip() == "":
            body_start += 1
        body_end = body_start
        while body_end < len(lines) and not lines[body_end].startswith("## "):
            body_end += 1
        return _split_paragraphs("\n".join(lines[body_start:body_end]))
    return []


def _normalized_fact_text(paragraph: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", paragraph, flags=re.DOTALL)
    statement = re.split(r"\s+Source:\s*", without_comments, maxsplit=1)[0]
    return re.sub(r"\s+", " ", statement).strip().casefold()


def _split_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", content
    lines = content.splitlines(keepends=True)
    if not lines or lines[0] != "---\n":
        return "", content
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[: index + 1]), "".join(lines[index + 1 :])
    return "", content


def _split_into_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content into (title, body) sections by ## headings."""
    sections = []
    current_title = ""
    current_lines = []
    for line in content.split("\n"):
        if line.startswith("## "):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))
    return sections


def _normalize_section_title(title: str) -> str:
    normalized = title.strip()
    while normalized.startswith("#"):
        normalized = normalized[1:].strip()
    return " ".join(normalized.casefold().split())


def _split_fenced_code_chunks(content: str) -> list[tuple[bool, str]]:
    chunks: list[tuple[bool, str]] = []
    current_lines: list[str] = []
    in_code_block = False

    def flush() -> None:
        if current_lines:
            chunks.append((in_code_block, "".join(current_lines)))

    for line in content.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            flush()
            current_lines = [line]
            in_code_block = not in_code_block
            flush()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append((in_code_block, "".join(current_lines)))
    return chunks


def _count_unprotected_occurrences(content: str, needle: str) -> int:
    count = 0
    for is_code_block, chunk in _split_fenced_code_chunks(content):
        if is_code_block or not needle:
            continue
        start = 0
        while True:
            index = chunk.find(needle, start)
            if index == -1:
                break
            count += 1
            start = index + len(needle)
    return count


def _replace_first_unprotected(content: str, old: str, new: str) -> tuple[str, bool]:
    if not old:
        return content, False
    updated_chunks: list[str] = []
    replaced = False
    for is_code_block, chunk in _split_fenced_code_chunks(content):
        if not is_code_block and not replaced and old in chunk:
            chunk = chunk.replace(old, new, 1)
            replaced = True
        updated_chunks.append(chunk)
    return "".join(updated_chunks), replaced


def _manual_review_reason(old_object: str) -> str:
    return f"Ambiguous match: {old_object!r} found in multiple sections or no section anchor available."


def _replace_old_object_in_section(
    content: str,
    old_object: str,
    replacement: str,
    *,
    section_title: str | None,
) -> _RenderedPatch:
    if not section_title:
        return _RenderedPatch(
            content=content,
            status=PatchPlanStatus.needs_manual_review,
            reason=_manual_review_reason(old_object),
        )

    frontmatter, body = _split_frontmatter(content)
    sections = _split_into_sections(body)
    normalized_title = _normalize_section_title(section_title)
    section_indices = [
        index for index, (title, _) in enumerate(sections) if _normalize_section_title(title) == normalized_title
    ]
    if len(section_indices) != 1:
        return _RenderedPatch(
            content=content,
            status=PatchPlanStatus.needs_manual_review,
            reason=_manual_review_reason(old_object),
        )

    target_index = section_indices[0]
    target_title, target_body = sections[target_index]
    if _count_unprotected_occurrences(target_body, old_object) < 1:
        return _RenderedPatch(
            content=content,
            status=PatchPlanStatus.needs_manual_review,
            reason=_manual_review_reason(old_object),
        )

    updated_section, replaced = _replace_first_unprotected(target_body, old_object, replacement)
    if not replaced:
        return _RenderedPatch(
            content=content,
            status=PatchPlanStatus.needs_manual_review,
            reason=_manual_review_reason(old_object),
        )

    sections[target_index] = (target_title, updated_section)
    updated_body = "\n".join(section_body for _, section_body in sections)
    return _RenderedPatch(content=f"{frontmatter}{updated_body}")
