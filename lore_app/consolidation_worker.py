from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .audit import AuditEntry, AuditLog, new_audit_entry
from .extraction import extract_from_captures
from .ledger import LedgerDB, utc_now
from .patch_planner import PatchPlanner
from .policy_engine import PolicyEngine
from .procedure_candidate import auto_propose_procedure_candidates
from .repository import LoreRepository, infer_kind, optional_string
from .schemas import (
    ConsolidationRunResult,
    ContextRef,
    ExtractionResult,
    PatchPlan,
    RollbackResult,
    ToolRef,
    TraceEntry,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import LoreConfig
    from .observability import MetricsCollector

logger = logging.getLogger("lore.consolidation")


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def run_auto_consolidation(app: Any) -> None:
    """Background, coalesced consolidation triggered off the capture path.

    Runs the consolidation worker so freshly captured memory becomes ledger
    candidates (recallable immediately) and durable pages (safe auto-apply)
    without a manual step. Non-blocking and self-coalescing: if a run is already
    in progress, it just flags a rerun so the in-flight run processes the newly
    captured work, never overlapping two runs.
    """
    state = app.state
    config = state.config
    if not getattr(config, "auto_consolidate", True):
        return
    lock = getattr(state, "auto_consolidate_lock", None)
    if lock is None or not lock.acquire(blocking=False):
        if lock is not None:
            state.auto_consolidate_rerun = True
        return
    try:
        worker = state.consolidation_worker
        batch_size = max(1, int(getattr(config, "auto_consolidate_batch_size", 25)))
        while True:
            state.auto_consolidate_rerun = False
            try:
                worker.run(dry_run=False, batch_size=batch_size, max_auto_apply=batch_size)
            except Exception:  # pragma: no cover - background best-effort
                logger.exception("Auto-consolidation run failed")
            if not getattr(state, "auto_consolidate_rerun", False):
                break
        try:
            auto_propose_procedure_candidates(app)
        except Exception:  # pragma: no cover - background best-effort
            logger.exception("Automatic procedure discovery failed")
    finally:
        lock.release()


class ConsolidationWorker:
    """Run extraction, patch planning, safe auto-apply, and rollback."""

    def __init__(
        self,
        repo: LoreRepository,
        ledger: LedgerDB,
        planner: PatchPlanner,
        config: LoreConfig,
        audit_log: AuditLog | None = None,
        llm_client_provider: Callable[[], Any] | None = None,
        metrics: MetricsCollector | None = None,
    ):
        self.repo = repo
        self.ledger = ledger
        self.planner = planner
        self.config = config
        self.audit_log = audit_log
        self.metrics = metrics
        self.policy_engine = planner.policy_engine or PolicyEngine(ledger)
        # Resolves the current LLM client at run time. The web app hot-swaps
        # app.state.llm_client on settings changes, so the worker must fetch the
        # live client per run rather than capture a stale reference.
        self._llm_client_provider = llm_client_provider

    def run(
        self,
        *,
        dry_run: bool = False,
        batch_size: int = 10,
        max_auto_apply: int = 5,
        force_reextract: bool = False,
        llm_client: Any | None = None,
    ) -> ConsolidationRunResult:
        """Run extraction, planning, and bounded safe auto-apply.

        ``llm_client`` overrides the configured provider for this run; when it is
        None the worker resolves the live client from ``llm_client_provider`` so
        LLM-backed extraction runs on the auto-consolidation/scheduled paths, not
        just the manual extraction route.
        """

        errors: list[str] = []
        batch_id = str(uuid.uuid4())
        extraction_result = None

        if force_reextract and not dry_run:
            try:
                self.ledger.reset_extraction()
            except Exception as exc:  # pragma: no cover - defensive boundary
                errors.append(f"re-extraction reset failed: {exc}")

        active_llm_client = llm_client
        if active_llm_client is None and self._llm_client_provider is not None:
            active_llm_client = self._llm_client_provider()

        try:
            extraction_result = extract_from_captures(
                self.repo,
                batch_size=batch_size,
                dry_run=dry_run,
                ledger_db=self.ledger,
                llm_client=active_llm_client,
            )
            batch_id = extraction_result.batch_id
        except Exception as exc:  # pragma: no cover - defensive boundary
            errors.append(f"extraction failed: {exc}")
            if self.metrics is not None:
                self.metrics.increment_extraction_failures()

        if not dry_run:
            if extraction_result is not None:
                try:
                    self._auto_supersede_contradictions(extraction_result)
                except Exception as exc:  # pragma: no cover - defensive boundary
                    errors.append(f"claim supersession sweep failed: {exc}")
            try:
                self._archive_floored_claims()
            except Exception as exc:  # pragma: no cover - defensive boundary
                errors.append(f"claim forget sweep failed: {exc}")

        plans: list[PatchPlan] = []
        try:
            if dry_run:
                # Dry-run previews plans for the just-extracted batch in a throwaway
                # ledger without touching real state.
                if extraction_result is not None:
                    plans = self._plan_dry_run(extraction_result)
            else:
                # Plan ALL un-planned candidate claims, not just this run's batch.
                # Candidates left over from a prior /api/extraction/run, or reinforced
                # under an older batch id (force_reextract), keep their original
                # batch_id and were silently stranded by the previous batch filter.
                plans = self.planner.plan_batch()
        except Exception as exc:
            errors.append(f"planning failed: {exc}")

        auto_applied = 0
        blocked_claims: list[dict[str, str]] = []
        for plan in plans:
            skip_apply = not plan.auto_appliable or self._page_kind(plan.target_page_id) in {"decision", "runbook"}
            if skip_apply:
                for decision in plan.policies_applied:
                    if not decision.passed and decision.policy_id.startswith("epistemic:"):
                        for candidate_id in plan.candidate_ids:
                            blocked_claims.append(
                                {
                                    "claim_id": candidate_id,
                                    "reason": decision.reason,
                                    "epistemic_status": decision.policy_id.split(":", 1)[1].split("-")[0]
                                    if ":" in decision.policy_id
                                    else "unknown",
                                }
                            )
            if dry_run or auto_applied >= max_auto_apply:
                continue
            if skip_apply:
                continue
            try:
                before_content = self._current_content(plan.target_page_id)
                self.planner.apply_plan(plan.plan_id)
                after_content = self._current_content(plan.target_page_id)
                self._record_apply_audit(plan, before_content, after_content)
                auto_applied += 1
            except Exception as exc:
                errors.append(f"apply failed for plan {plan.plan_id}: {exc}")
                if self.metrics is not None:
                    self.metrics.increment_apply_failures()
                self.ledger.store_trace(
                    TraceEntry(
                        trace_id="",
                        actor="consolidation-worker",
                        reason_summary=f"Apply failed for plan {plan.plan_id}: {exc}",
                        context_refs=[
                            ContextRef(type="plan", id=plan.plan_id),
                            ContextRef(type="page", id=plan.target_page_id),
                        ],
                        tool_refs=[ToolRef(tool="consolidation-worker", action="apply")],
                        status="failed",
                        outcome=f"plan_id={plan.plan_id}, error={exc}",
                        related_ids={"plan_id": plan.plan_id, "page_id": plan.target_page_id},
                    )
                )

        candidates_extracted = 0
        captures_processed = 0
        if extraction_result is not None:
            captures_processed = len(extraction_result.source_capture_ids)
            candidates_extracted = (
                len(extraction_result.entities)
                + len(extraction_result.claims)
                + len(extraction_result.edges)
                + len(extraction_result.invalidations)
            )

        result = ConsolidationRunResult(
            batch_id=batch_id,
            captures_processed=captures_processed,
            candidates_extracted=candidates_extracted,
            plans_generated=len(plans),
            auto_applied=auto_applied,
            review_required=max(0, len(plans) - auto_applied),
            errors=errors,
            dry_run=dry_run,
            blocked_claims=blocked_claims,
        )
        if not dry_run:
            self.ledger.store_consolidation_run(result, status="completed" if not errors else "completed_with_errors")
            if self.metrics is not None:
                # Vault-wide backlog gauge: the real manual-review queue plus
                # plans still pending a decision, not just this run's delta.
                try:
                    plans_by_status = self.ledger.get_consolidation_status().get("plans_by_status") or {}
                    backlog = int(plans_by_status.get("needs_manual_review", 0)) + int(
                        plans_by_status.get("pending", 0)
                    )
                    self.metrics.set_consolidation_backlog(backlog)
                except Exception:  # pragma: no cover - defensive gauge update
                    logger.exception("Failed to update consolidation backlog gauge.")
            self.ledger.store_trace(
                TraceEntry(
                    trace_id="",
                    actor="consolidation-worker",
                    reason_summary=(
                        f"Consolidation run: {result.captures_processed} captures, "
                        f"{result.plans_generated} plans, {result.auto_applied} auto-applied, "
                        f"{result.review_required} review-required"
                    ),
                    context_refs=[],
                    tool_refs=[ToolRef(tool="consolidation-worker", action="run")],
                    status="completed",
                    outcome=f"batch_id={result.batch_id}",
                    related_ids={"task_id": f"consolidation-{result.batch_id}"},
                )
            )
        return result

    def _auto_supersede_contradictions(self, extraction_result: ExtractionResult) -> int:
        superseded = 0
        for claim in extraction_result.claims:
            new_row = self.ledger.find_matching_claim(claim)
            if new_row is None:
                continue
            new_id = str(new_row["candidate_id"])
            for old_row in self.ledger.find_contradicting_claims(claim):
                old_id = str(old_row["candidate_id"])
                if old_id == new_id:
                    continue
                decision = self.policy_engine.evaluate_claim_supersession(old_row, new_row)
                policy_ref = f"{decision.policy_id}:{'pass' if decision.passed else 'fail'}"
                if decision.passed:
                    reason = f"Auto-superseded by strictly newer claim under {decision.policy_id}."
                    self.ledger.supersede_candidate(old_id, new_id, reason, actor=claim.actor)
                    superseded += 1
                    outcome = f"superseded {old_id} with {new_id}"
                    self._record_candidate_lifecycle_audit(
                        "claim.supersede",
                        old_id,
                        {
                            "old_candidate_id": old_id,
                            "new_candidate_id": new_id,
                            "policy": decision.model_dump(mode="json"),
                        },
                    )
                else:
                    outcome = f"review retained {old_id}: {decision.reason}"
                self.ledger.store_trace(
                    TraceEntry(
                        trace_id="",
                        actor="consolidation-worker",
                        reason_summary=f"Evaluated automatic claim supersession: {decision.reason}",
                        context_refs=[
                            ContextRef(type="candidate", id=old_id),
                            ContextRef(type="candidate", id=new_id),
                        ],
                        tool_refs=[ToolRef(tool="consolidation-worker", action="auto-supersede")],
                        policy_refs=[policy_ref],
                        status="completed",
                        outcome=outcome,
                        related_ids={"candidate_id": old_id, "replacement_candidate_id": new_id},
                    )
                )
        return superseded

    def _archive_floored_claims(self) -> int:
        floor_days = max(0, int(getattr(self.config, "claim_forget_after_floor_days", 30)))
        archived_ids = self.ledger.archive_floored_claims(floor_days)
        for candidate_id in archived_ids:
            self.ledger.store_trace(
                TraceEntry(
                    trace_id="",
                    actor="consolidation-worker",
                    reason_summary=f"Archived claim after {floor_days} days at the decay floor.",
                    context_refs=[ContextRef(type="candidate", id=candidate_id)],
                    tool_refs=[ToolRef(tool="consolidation-worker", action="forget")],
                    policy_refs=["claim-lifecycle:forget-v1:pass"],
                    status="completed",
                    outcome=f"archived {candidate_id}",
                    related_ids={"candidate_id": candidate_id},
                )
            )
            self._record_candidate_lifecycle_audit(
                "claim.archive",
                candidate_id,
                {"candidate_id": candidate_id, "floor_days": floor_days},
            )
        return len(archived_ids)

    def _record_candidate_lifecycle_audit(
        self,
        operation: str,
        candidate_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            new_audit_entry(
                actor="consolidation_worker",
                operation=operation,
                page_id=f"candidate:{candidate_id}",
                summary=json.dumps(payload, sort_keys=True),
                diff_size=0,
            )
        )

    def _plan_dry_run(self, extraction_result: ExtractionResult) -> list[PatchPlan]:
        """Generate patch plans from dry-run extraction without touching the real ledger."""

        with tempfile.TemporaryDirectory(prefix="lore-dry-run-") as temp_dir:
            dry_ledger = LedgerDB(Path(temp_dir) / "ledger.db")
            dry_ledger.initialize()
            try:
                from .policy_engine import PolicyEngine

                dry_ledger.store_extraction_result(extraction_result)
                dry_planner = PatchPlanner(
                    self.repo,
                    dry_ledger,
                    self.audit_log,
                    search_index=self.planner.search_index,
                    vector_store=self.planner.vector_store,
                    graph_cache=self.planner.graph_cache,
                    policy_engine=PolicyEngine(dry_ledger) if self.planner.policy_engine is not None else None,
                )
                return dry_planner.plan_batch(batch_id=extraction_result.batch_id)
            finally:
                dry_ledger.close()

    def rollback(self, plan_id: str) -> RollbackResult:
        """Restore a page to the content captured before a worker-applied plan."""

        plan_row = self.ledger.get_patch_plan(plan_id)
        if plan_row is None:
            raise ValueError(f"Patch plan {plan_id} not found")

        audit_payload = self._find_rollback_payload(plan_id)
        if audit_payload is None:
            raise ValueError(f"No rollback audit record found for patch plan {plan_id}")

        page_id = str(audit_payload.get("page_id") or plan_row["target_page_id"])
        before_content = str(audit_payload.get("before_content") or "")
        if not before_content:
            raise ValueError(f"Rollback audit record for patch plan {plan_id} has no before_content")

        current_content = self._current_content(page_id)
        self.repo.upsert_page(page_id, before_content)
        self.planner._reindex_page(page_id)
        rolled_back_at = utc_now()
        after_content = self._current_content(page_id)
        self.ledger.update_plan_status(plan_id, "rolled_back", rejected_at=rolled_back_at)
        if plan_row.get("trace_id"):
            plan_trace = self.ledger.get_trace(plan_row["trace_id"])
            if plan_trace:
                self.ledger.store_trace(
                    plan_trace.model_copy(
                        update={
                            "status": "completed",
                            "outcome": f"rolled_back: plan {plan_id}",
                            "updated_at": utc_now(),
                        }
                    )
                )
        self.ledger.store_trace(
            TraceEntry(
                trace_id="",
                actor="consolidation-worker",
                reason_summary=f"Rolled back plan {plan_id} on page {page_id}",
                context_refs=[ContextRef(type="plan", id=plan_id), ContextRef(type="page", id=page_id)],
                tool_refs=[ToolRef(tool="consolidation-worker", action="rollback")],
                status="completed",
                outcome=f"plan_id={plan_id}, page={page_id}",
                related_ids={"plan_id": plan_id, "page_id": page_id},
            )
        )
        self._record_rollback_audit(plan_id, page_id, current_content, after_content)

        return RollbackResult(
            plan_id=plan_id,
            page_id=page_id,
            before_hash=_content_hash(current_content),
            after_hash=_content_hash(after_content),
            rolled_back_at=rolled_back_at,
        )

    def status(self) -> dict[str, Any]:
        """Return consolidation status enriched with repository capture counts."""

        status = self.ledger.get_consolidation_status()
        extraction_status = self.ledger.get_extraction_status()
        candidate_counts = self.ledger.get_candidate_status_counts()
        captures = self.repo.list_pages(kind="capture")
        status["total_captures"] = len([page for page in captures if page.status in {"draft", "accepted"}])
        status["total_draft_captures"] = len([page for page in captures if page.status == "draft"])
        status["total_extracted_captures"] = len([page for page in captures if page.status == "accepted"])
        status["total_extracted"] = extraction_status.total_extracted
        status["candidates"] = sum(candidate_counts.values())
        status["candidates_by_status"] = candidate_counts
        return status

    def _page_kind(self, page_id: str) -> str:
        page = self.repo.read_page(page_id)
        if page is not None:
            return optional_string(page.frontmatter.get("kind")) or infer_kind(page_id)
        return infer_kind(page_id)

    def _current_content(self, page_id: str) -> str:
        page = self.repo.read_page(page_id)
        return page.content if page is not None else ""

    def _record_apply_audit(self, plan: PatchPlan, before_content: str, after_content: str) -> None:
        if self.audit_log is None:
            return
        summary = json.dumps(
            {
                "plan_id": plan.plan_id,
                "candidate_ids": plan.candidate_ids,
                "page_id": plan.target_page_id,
                "before_hash": _content_hash(before_content),
                "after_hash": _content_hash(after_content),
                "before_content": before_content,
            },
            sort_keys=True,
        )
        self.audit_log.record(
            new_audit_entry(
                actor="consolidation_worker",
                operation="consolidation.apply",
                page_id=plan.target_page_id,
                summary=summary,
                diff_size=abs(len(after_content) - len(before_content)),
            )
        )

    def _record_rollback_audit(
        self,
        plan_id: str,
        page_id: str,
        before_content: str,
        after_content: str,
    ) -> None:
        if self.audit_log is None:
            return
        summary = json.dumps(
            {
                "plan_id": plan_id,
                "page_id": page_id,
                "before_hash": _content_hash(before_content),
                "after_hash": _content_hash(after_content),
            },
            sort_keys=True,
        )
        self.audit_log.record(
            new_audit_entry(
                actor="consolidation_worker",
                operation="consolidation.rollback",
                page_id=page_id,
                summary=summary,
                diff_size=abs(len(after_content) - len(before_content)),
            )
        )

    def _find_rollback_payload(self, plan_id: str) -> dict[str, Any] | None:
        if self.audit_log is None:
            return None
        for entry in self.audit_log.query(limit=10_000):
            payload = _summary_payload(entry)
            if payload.get("plan_id") == plan_id and payload.get("before_content") is not None:
                return payload
        return None


def _summary_payload(entry: AuditEntry) -> dict[str, Any]:
    try:
        payload = json.loads(entry.summary)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
