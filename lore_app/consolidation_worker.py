from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .audit import AuditEntry, AuditLog, new_audit_entry
from .config import LoreConfig
from .extraction import extract_from_captures
from .ledger import LedgerDB, utc_now
from .patch_planner import PatchPlanner
from .repository import LoreRepository, infer_kind, optional_string
from .schemas import ConsolidationRunResult, PatchPlan, RollbackResult, ToolRef, TraceEntry


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ConsolidationWorker:
    """Run extraction, patch planning, safe auto-apply, and rollback."""

    def __init__(
        self,
        repo: LoreRepository,
        ledger: LedgerDB,
        planner: PatchPlanner,
        config: LoreConfig,
        audit_log: AuditLog | None = None,
    ):
        self.repo = repo
        self.ledger = ledger
        self.planner = planner
        self.config = config
        self.audit_log = audit_log

    def run(
        self,
        *,
        dry_run: bool = False,
        batch_size: int = 10,
        max_auto_apply: int = 5,
        force_reextract: bool = False,
    ) -> ConsolidationRunResult:
        """Run extraction, planning, and bounded safe auto-apply."""

        errors: list[str] = []
        batch_id = str(uuid.uuid4())
        extraction_result = None

        if force_reextract and not dry_run:
            try:
                self.ledger.reset_extraction()
            except Exception as exc:  # pragma: no cover - defensive boundary
                errors.append(f"re-extraction reset failed: {exc}")

        try:
            extraction_result = extract_from_captures(
                self.repo,
                batch_size=batch_size,
                dry_run=False,
                ledger_db=self.ledger,
            )
            batch_id = extraction_result.batch_id
        except Exception as exc:  # pragma: no cover - defensive boundary
            errors.append(f"extraction failed: {exc}")

        plans: list[PatchPlan] = []
        if extraction_result is not None and extraction_result.source_capture_ids:
            try:
                plans = self.planner.plan_batch(batch_id=batch_id)
            except Exception as exc:
                errors.append(f"planning failed: {exc}")

        auto_applied = 0
        for plan in plans:
            if dry_run or auto_applied >= max_auto_apply:
                continue
            if not plan.auto_appliable or self._page_kind(plan.target_page_id) in {"decision", "runbook"}:
                continue
            try:
                before_content = self._current_content(plan.target_page_id)
                apply_result = self.planner.apply_plan(plan.plan_id)
                after_content = self._current_content(plan.target_page_id)
                self._record_apply_audit(plan, before_content, after_content)
                auto_applied += 1
            except Exception as exc:
                errors.append(f"apply failed for plan {plan.plan_id}: {exc}")

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
        )
        self.ledger.store_consolidation_run(result, status="completed" if not errors else "completed_with_errors")
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
        captures = self.repo.list_pages(kind="capture")
        status["total_captures"] = len(
            [page for page in captures if page.status in {"draft", "accepted"}]
        )
        status["total_draft_captures"] = len([page for page in captures if page.status == "draft"])
        status["total_extracted_captures"] = len([page for page in captures if page.status == "accepted"])
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
