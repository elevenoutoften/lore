from __future__ import annotations

import contextlib
import json

import pytest
from pydantic import ValidationError

import lore_app.ledger as ledger_module
from lore_app.ledger import LedgerDB
from lore_app.patch_planner import PatchPlanner
from lore_app.policy_engine import PolicyEngine
from lore_app.repository import LoreRepository
from lore_app.schemas import ExtractedClaim, ExtractionResult, PatchOperation, PolicyRule


@pytest.fixture(autouse=True)
def patch_ledger_row_decoder(monkeypatch):
    def decode_row(row):
        decoded = dict(row)
        for key in ("content_json", "source_capture_ids", "source_page_ids", "candidate_ids", "policies_applied"):
            if key in decoded and isinstance(decoded[key], str):
                with contextlib.suppress(json.JSONDecodeError):
                    decoded[key] = json.loads(decoded[key])
        if "auto_appliable" in decoded:
            decoded["auto_appliable"] = bool(decoded["auto_appliable"])
        return decoded

    monkeypatch.setattr(ledger_module, "_decode_row", decode_row)


@pytest.fixture
def ledger(tmp_path) -> LedgerDB:
    db = LedgerDB(tmp_path / "ledger.db")
    db.initialize()
    return db


def decision_by_id(decisions, policy_id: str):
    return next(decision for decision in decisions if decision.policy_id == policy_id)


def make_policy(**overrides) -> PolicyRule:
    defaults = {
        "policy_id": "valid-name:v1",
        "name": "Valid policy",
        "description": "Policy used for validation tests.",
        "gate": "auto-apply",
        "condition_kind": [],
        "condition_operation": [PatchOperation.insert_new_fact.value],
        "effect_pass": "allow",
        "effect_fail": "review",
        "fail_reason_template": "Policy failed for {page_id}",
        "version": 1,
        "enabled": True,
    }
    defaults.update(overrides)
    return PolicyRule(**defaults)


def test_default_policies_reproduce_patch_planner_behavior(ledger):
    policies = ledger.list_policies(enabled_only=True)
    assert len(policies) == 4
    assert {policy.policy_id for policy in policies} == {
        "auto-apply:v1",
        "protected-surface:v1",
        "contradiction-review:v1",
        "risk-high:v1",
    }

    safe_insert = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
    )
    assert decision_by_id(safe_insert, "auto-apply:v1").passed is True
    assert decision_by_id(safe_insert, "protected-surface:v1").passed is True

    decision_insert = PolicyEngine(ledger).evaluate(
        page_id="decisions/routing",
        page_kind="decision",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
    )
    assert decision_by_id(decision_insert, "auto-apply:v1").passed is False
    assert decision_by_id(decision_insert, "protected-surface:v1").passed is False

    risky_update = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.update_existing_fact,
        has_contradictions=False,
        high_confidence=True,
    )
    assert decision_by_id(risky_update, "risk-high:v1").passed is False

    contradictory_mark_stale = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.mark_stale,
        has_contradictions=True,
        high_confidence=True,
    )
    assert decision_by_id(contradictory_mark_stale, "contradiction-review:v1").passed is False


def test_review_required_policy_can_fail(ledger):
    """A matched review-required policy must fail its gate so effect_fail can fire."""
    ledger.store_policy(
        make_policy(
            policy_id="needs-review:v1",
            name="Needs review",
            gate="review-required",
            condition_kind=["procedure"],
            condition_operation=[PatchOperation.insert_new_fact.value],
        )
    )

    decisions = PolicyEngine(ledger).evaluate(
        page_id="procedures/deploy",
        page_kind="procedure",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
    )

    assert decision_by_id(decisions, "needs-review:v1").passed is False


def test_auto_apply_policy_passes_for_high_confidence_no_contradictions(ledger):
    decisions = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
    )

    assert decision_by_id(decisions, "auto-apply:v1").passed is True
    assert decision_by_id(decisions, "protected-surface:v1").passed is True


def test_auto_apply_policy_blocks_for_decision_pages(ledger):
    decisions = PolicyEngine(ledger).evaluate(
        page_id="decisions/routing",
        page_kind="decision",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
    )

    assert decision_by_id(decisions, "auto-apply:v1").passed is False
    protected = decision_by_id(decisions, "protected-surface:v1")
    assert protected.passed is False
    assert "requires review" in protected.reason


def test_contradiction_review_policy_blocks_when_contradictions_exist(ledger):
    decisions = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.mark_stale,
        has_contradictions=True,
        high_confidence=True,
    )

    assert decision_by_id(decisions, "contradiction-review:v1").passed is False


def test_risk_assessment_policy_blocks_risky_operations(ledger):
    decisions = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.update_existing_fact,
        has_contradictions=False,
        high_confidence=True,
    )

    assert decision_by_id(decisions, "risk-high:v1").passed is False


def test_disabled_policy_is_skipped(ledger):
    assert ledger.delete_policy("auto-apply:v1") is True

    decisions = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
    )

    assert "auto-apply:v1" not in {decision.policy_id for decision in decisions}


def test_policy_version_change(ledger):
    policy = PolicyRule(
        policy_id="custom-auto:v1",
        name="Custom auto policy",
        gate="auto-apply",
        condition_kind=["service"],
        condition_operation=[PatchOperation.insert_new_fact.value],
        version=1,
    )
    ledger.store_policy(policy)
    assert ledger.get_policy("custom-auto:v1").version == 1

    ledger.store_policy(policy.model_copy(update={"condition_kind": ["decision"], "version": 2}))
    stored = ledger.get_policy("custom-auto:v1")
    assert stored is not None
    assert stored.version == 2
    assert stored.condition_kind == ["decision"]
    service_decisions = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
        policy_ids=["custom-auto:v1"],
    )
    decision_decisions = PolicyEngine(ledger).evaluate(
        page_id="decisions/routing",
        page_kind="decision",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
        policy_ids=["custom-auto:v1"],
    )
    assert service_decisions == []
    assert decision_decisions[0].passed is False


def test_missing_policy_fallback(ledger):
    ledger.connection.execute("DELETE FROM policies")
    ledger.connection.commit()

    decisions = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
    )

    assert decisions == []


def test_invalid_policy_id_returns_empty(ledger):
    decisions = PolicyEngine(ledger).evaluate(
        page_id="services/lore",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
        policy_ids=["nonexistent:v1"],
    )

    assert decisions == []


def test_reject_invalid_policy_id_format():
    with pytest.raises(ValidationError):
        make_policy(policy_id="bad id:v1")
    with pytest.raises(ValidationError):
        make_policy(policy_id="UPPER:v1")
    with pytest.raises(ValidationError):
        make_policy(policy_id="no-version")

    policy = make_policy(policy_id="valid-name:v1")
    assert policy.policy_id == "valid-name:v1"


def test_reject_invalid_effect_values():
    with pytest.raises(ValidationError):
        make_policy(effect_pass="invalid")
    with pytest.raises(ValidationError):
        make_policy(effect_fail="invalid")


def test_reject_invalid_condition_operation():
    with pytest.raises(ValidationError):
        make_policy(condition_operation=["nonexistent_op"])


def test_store_and_retrieve_policy_preserves_all_fields(ledger):
    policy = PolicyRule(
        policy_id="custom-gate:v1",
        name="Custom review gate",
        description="Preserve every stored field.",
        gate="review-required",
        condition_kind=["procedure"],
        condition_operation=[PatchOperation.create_stub_page.value],
        effect_pass="allow",
        effect_fail="escalate",
        fail_reason_template="Procedure page {page_id} requires escalation.",
        version=3,
        enabled=True,
    )

    ledger.store_policy(policy)
    stored = ledger.get_policy("custom-gate:v1")

    assert stored is not None
    assert stored.model_dump() == policy.model_dump()


def test_patch_planner_uses_policy_engine(tmp_path, ledger):
    repo = LoreRepository(tmp_path / "pages")
    repo.ensure_root()
    repo.upsert_page(
        "decisions/routing",
        """---
title: Routing
kind: decision
visibility: internal
status: accepted
---

# Routing

## Facts
""",
    )
    store_claim(ledger, "batch-policy", "decisions/routing", "Routing requires review")

    planner = PatchPlanner(repo, ledger, policy_engine=PolicyEngine(ledger))
    plans = planner.plan_batch(batch_id="batch-policy")

    assert len(plans) == 1
    plan = plans[0]
    assert plan.policies_applied
    assert decision_by_id(plan.policies_applied, "protected-surface:v1").passed is False
    assert plan.auto_appliable is False
    trace = ledger.get_trace(plan.trace_id or "")
    assert trace is not None
    assert "protected-surface:v1:fail" in trace.policy_refs


def test_patch_planner_without_policy_engine_uses_defaults(tmp_path, ledger):
    repo = LoreRepository(tmp_path / "pages")
    repo.ensure_root()
    repo.upsert_page(
        "decisions/routing",
        """---
title: Routing
kind: decision
visibility: internal
status: accepted
---

# Routing

## Facts
""",
    )
    store_claim(ledger, "batch-defaults", "decisions/routing", "Routing still requires review")

    planner = PatchPlanner(repo, ledger)
    plans = planner.plan_batch(batch_id="batch-defaults")

    assert len(plans) == 1
    assert plans[0].policies_applied == []
    assert plans[0].auto_appliable is False
    trace = ledger.get_trace(plans[0].trace_id or "")
    assert trace is not None
    assert "patch-planner:protected-surface" in trace.policy_refs


def store_claim(ledger: LedgerDB, batch_id: str, page_id: str, obj: str) -> None:
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id=batch_id,
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=[f"inbox/{batch_id}"],
            claims=[
                ExtractedClaim(
                    subject=page_id,
                    predicate="states",
                    object=obj,
                    confidence="high",
                    actor="nyx",
                    lane="project",
                    observed_at="2026-05-10T00:00:00+00:00",
                    source_page_ids=[page_id],
                )
            ],
            entities=[],
            edges=[],
            invalidations=[],
        )
    )


def test_assumption_blocked_by_epistemic_gate(ledger):
    engine = PolicyEngine(ledger)
    decisions = engine.evaluate(
        page_id="services/test",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
        epistemic_status="assumption",
    )
    assert any(d.policy_id == "epistemic:assumption-block" and not d.passed for d in decisions)


def test_retrieved_accepted_by_epistemic_gate(ledger):
    engine = PolicyEngine(ledger)
    decisions = engine.evaluate(
        page_id="services/test",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
        epistemic_status="retrieved",
    )
    assert not any(d.policy_id.startswith("epistemic:") and not d.passed for d in decisions)


def test_inferred_without_trace_blocked(ledger):
    engine = PolicyEngine(ledger)
    decisions = engine.evaluate(
        page_id="services/test",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
        epistemic_status="inferred",
        trace_id=None,
    )
    assert any(d.policy_id == "epistemic:inferred-review" and not d.passed for d in decisions)


def test_inferred_with_trace_accepted(ledger):
    engine = PolicyEngine(ledger)
    decisions = engine.evaluate(
        page_id="services/test",
        page_kind="service",
        operation=PatchOperation.insert_new_fact,
        has_contradictions=False,
        high_confidence=True,
        epistemic_status="inferred",
        trace_id="trace-001",
    )
    assert not any(d.policy_id.startswith("epistemic:") and not d.passed for d in decisions)
