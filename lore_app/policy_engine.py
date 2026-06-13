from __future__ import annotations

from typing import TYPE_CHECKING

from .schemas import PatchOperation, PolicyDecision, PolicyRule

if TYPE_CHECKING:
    from .ledger import LedgerDB


class PolicyEngine:
    """Evaluates policy rules against patch plan contexts."""

    def __init__(self, ledger: LedgerDB):
        self.ledger = ledger

    def evaluate(
        self,
        page_id: str,
        page_kind: str,
        operation: PatchOperation,
        has_contradictions: bool,
        high_confidence: bool,
        *,
        policy_ids: list[str] | None = None,
        epistemic_status: str | None = None,
        trace_id: str | None = None,
    ) -> list[PolicyDecision]:
        if policy_ids:
            policies = [policy for policy_id in policy_ids if (policy := self.ledger.get_policy(policy_id)) is not None]
        else:
            policies = self.ledger.list_policies(enabled_only=True)

        decisions: list[PolicyDecision] = []
        for policy in policies:
            if not self._applies(policy, page_kind, operation):
                continue

            passed = self._evaluate_condition(
                policy,
                page_kind,
                operation,
                has_contradictions,
                high_confidence,
            )
            if passed:
                reason = policy.effect_pass
            else:
                try:
                    reason = policy.fail_reason_template.format(
                        kind=page_kind,
                        operation=operation.value,
                        page_id=page_id,
                        policy_id=policy.policy_id,
                        gate=policy.gate,
                    )
                except (KeyError, ValueError):
                    reason = ""
                if not reason:
                    reason = f"Policy {policy.policy_id} failed for gate {policy.gate}"

            decisions.append(
                PolicyDecision(
                    policy_id=policy.policy_id,
                    gate=policy.gate,
                    passed=passed,
                    reason=reason,
                )
            )

        if epistemic_status == "assumption":
            decisions.append(
                PolicyDecision(
                    policy_id="epistemic:assumption-block",
                    gate="epistemic-gate",
                    passed=False,
                    reason="Assumption-labeled claims require human review before auto-apply.",
                )
            )
        elif epistemic_status == "inferred" and not trace_id:
            decisions.append(
                PolicyDecision(
                    policy_id="epistemic:inferred-review",
                    gate="epistemic-gate",
                    passed=False,
                    reason="Inferred claims without a supporting trace require review.",
                )
            )

        return decisions

    def _applies(self, policy: PolicyRule, page_kind: str, operation: PatchOperation) -> bool:
        if policy.gate == "protected-surface":
            return True
        if policy.gate == "risk-assessment" and policy.condition_kind and policy.condition_operation:
            return page_kind in policy.condition_kind or operation.value in policy.condition_operation
        if policy.condition_kind and page_kind not in policy.condition_kind:
            return False
        return not (policy.condition_operation and operation.value not in policy.condition_operation)

    def _evaluate_condition(
        self,
        policy: PolicyRule,
        page_kind: str,
        operation: PatchOperation,
        has_contradictions: bool,
        high_confidence: bool,
    ) -> bool:
        if policy.gate == "auto-apply":
            protected_kinds = set(policy.condition_kind) if policy.condition_kind else {"decision", "runbook"}
            return high_confidence and not has_contradictions and page_kind not in protected_kinds
        if policy.gate == "protected-surface":
            protected_kinds = set(policy.condition_kind) if policy.condition_kind else {"decision", "runbook"}
            return page_kind not in protected_kinds
        if policy.gate == "contradiction-review":
            return not has_contradictions
        if policy.gate == "risk-assessment":
            protected_kinds = set(policy.condition_kind) if policy.condition_kind else {"decision", "runbook"}
            risky_ops = {"update_existing_fact", "mark_stale"}
            return operation.value not in risky_ops and page_kind not in protected_kinds
        # review-required: _applies already matched the page/operation, so fail
        # the gate to route the matched plan to review (its effect_fail).
        return policy.gate != "review-required"
