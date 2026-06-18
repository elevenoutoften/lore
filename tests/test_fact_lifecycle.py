from __future__ import annotations

from dataclasses import dataclass

from lore_app.audit import AuditLog
from lore_app.config import LoreConfig
from lore_app.consolidation_worker import ConsolidationWorker
from lore_app.ledger import LedgerDB
from lore_app.patch_planner import PatchPlanner
from lore_app.policy_engine import PolicyEngine
from lore_app.repository import LoreRepository
from lore_app.schemas import ExtractedClaim, ExtractionResult


@dataclass
class LifecycleContext:
    ledger: LedgerDB
    worker: ConsolidationWorker
    audit_log: AuditLog


def _context(tmp_path) -> LifecycleContext:
    config = LoreConfig()
    config.content_dir = tmp_path / "pages"
    config.ledger_db = tmp_path / "ledger.db"
    config.claim_forget_after_floor_days = 30
    repo = LoreRepository(config.content_dir)
    repo.ensure_root()
    ledger = LedgerDB(config.ledger_db)
    ledger.initialize()
    audit_log = AuditLog(tmp_path / "audit")
    planner = PatchPlanner(repo, ledger, audit_log, policy_engine=PolicyEngine(ledger))
    worker = ConsolidationWorker(repo, ledger, planner, config, audit_log)
    return LifecycleContext(ledger=ledger, worker=worker, audit_log=audit_log)


def _result(batch_id: str, capture_id: str, claim: ExtractedClaim) -> ExtractionResult:
    return ExtractionResult(
        batch_id=batch_id,
        processed_at="2026-06-18T00:00:00+00:00",
        source_capture_ids=[capture_id],
        claims=[claim],
        entities=[],
        edges=[],
        invalidations=[],
    )


def test_cross_source_corroboration_reinforces_one_claim_and_traces(tmp_path):
    ctx = _context(tmp_path)
    first = ExtractedClaim(
        subject="services/lore",
        predicate="uses",
        object="SQLite",
        confidence="high",
        actor="nyx",
        source_page_ids=["inbox/source-a"],
    )
    second = first.model_copy(update={"source_page_ids": ["inbox/source-b"]})

    ctx.ledger.store_extraction_result(_result("batch-a", "inbox/source-a", first))
    ctx.ledger.store_extraction_result(_result("batch-b", "inbox/source-b", second))

    [row] = ctx.ledger.get_candidates(candidate_type="claim")
    assert row["strength"] == 0.55
    assert row["source_capture_ids"] == ["inbox/source-a", "inbox/source-b"]
    assert row["source_page_ids"] == ["inbox/source-a", "inbox/source-b"]
    traces = ctx.ledger.list_traces(actor="nyx")
    assert any(trace.tool_refs[0].action == "reinforce" for trace in traces)
    assert any("claim-lifecycle:corroborate-v1:pass" in trace.policy_refs for trace in traces)


def test_newer_contradiction_auto_supersedes_with_policy_trace_and_audit(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    old_claim = ExtractedClaim(
        subject="services/lore",
        predicate="uses database",
        object="MySQL",
        confidence="medium",
        actor="nyx",
        valid_from="2026-01-01",
        source_page_ids=["inbox/old"],
    )
    new_claim = old_claim.model_copy(
        update={
            "object": "Postgres",
            "confidence": "high",
            "valid_from": "2026-06-15",
            "source_page_ids": ["inbox/new"],
        }
    )
    old_result = _result("batch-old", "inbox/old", old_claim)
    new_result = _result("batch-new", "inbox/new", new_claim)
    ctx.ledger.store_extraction_result(old_result)
    old_id = ctx.ledger.find_matching_claim(old_claim)["candidate_id"]

    def extract_new_claim(*_args, **_kwargs):
        ctx.ledger.store_extraction_result(new_result)
        return new_result

    monkeypatch.setattr("lore_app.consolidation_worker.extract_from_captures", extract_new_claim)
    ctx.worker.run(dry_run=False, max_auto_apply=0)
    new_id = ctx.ledger.get_candidates(candidate_type="claim", status="candidate")[0]["candidate_id"]

    rows = {row["candidate_id"]: row for row in ctx.ledger.get_candidates(candidate_type="claim")}
    assert rows[old_id]["status"] == "superseded"
    assert rows[old_id]["superseded_by"] == new_id
    assert rows[old_id]["valid_until"] is not None
    traces = ctx.ledger.list_traces(actor="consolidation-worker")
    assert any(trace.tool_refs[0].action == "auto-supersede" for trace in traces)
    assert any("claim-lifecycle:supersede-v1:pass" in trace.policy_refs for trace in traces)
    assert any(entry.operation == "claim.supersede" for entry in ctx.audit_log.query(limit=20))


def test_floored_claim_is_archived_after_configured_window_with_records(tmp_path):
    ctx = _context(tmp_path)
    claim = ExtractedClaim(
        subject="services/lore",
        predicate="remembers",
        object="A withered fact",
        confidence="low",
        actor="nyx",
        source_page_ids=["inbox/withered"],
    )
    ctx.ledger.store_extraction_result(_result("batch-withered", "inbox/withered", claim))
    candidate_id = ctx.ledger.find_matching_claim(claim)["candidate_id"]
    ctx.ledger.connection.execute(
        "UPDATE extraction_candidates SET strength = 0.01, last_decayed_at = ? WHERE candidate_id = ?",
        ("2026-01-01T00:00:00+00:00", candidate_id),
    )
    ctx.ledger.connection.commit()

    assert ctx.worker._archive_floored_claims() == 1

    [row] = ctx.ledger.get_candidates(candidate_type="claim")
    assert row["status"] == "archived"
    assert "30 days" in row["invalidation_reason"]
    traces = ctx.ledger.list_traces(actor="consolidation-worker")
    assert any(trace.tool_refs[0].action == "forget" for trace in traces)
    assert any("claim-lifecycle:forget-v1:pass" in trace.policy_refs for trace in traces)
    assert any(entry.operation == "claim.archive" for entry in ctx.audit_log.query(limit=20))
