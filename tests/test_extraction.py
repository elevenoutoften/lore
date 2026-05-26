from __future__ import annotations

import pytest
from pydantic import ValidationError
from unittest import mock

from lore_app.extraction import compute_extraction_hash, extract_from_captures, get_unprocessed_captures
from lore_app.ledger import LedgerDB
from lore_app.llm_provider import FallbackLLMClient, LLMError
from lore_app.repository import LoreRepository
from lore_app.schemas import (
    ExtractedClaim,
    ExtractedEdge,
    ExtractedEntity,
    ExtractedInvalidation,
    ExtractionRequest,
    ExtractionResult,
)


def add_capture(repo: LoreRepository, page_id: str = "inbox/2026-05-10/lore-ledger") -> str:
    repo.upsert_page(
        page_id,
        """---
title: Lore Ledger extraction
kind: capture
visibility: internal
status: draft
summary: Lore stores structured extraction candidates in a SQLite ledger.
confidence: high
actor: nyx
lane: project
observed_at: 2026-05-10T00:00:00+00:00
valid_from: 2026-05-10
source_task: flow_000375
decision_id: decisions/memory-policy
trace_id: trace-extraction-001
policies_applied:
  - L-MEM-03
suggested_target_page: services/lore
related:
  - projects/example-project
---

# Lore Ledger extraction

Lore now links [[Workflow Engine|services/workflow-engine]] and [[projects/example-project]] during extraction.

Contradicts: Lore only stores captures as Markdown.
""",
    )
    return page_id


def make_ledger(tmp_path) -> LedgerDB:
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    return ledger


def extraction_result(batch_id: str, capture_id: str, fact: str) -> ExtractionResult:
    return ExtractionResult(
        batch_id=batch_id,
        processed_at="2026-05-10T00:00:00+00:00",
        source_capture_ids=[capture_id],
        claims=[
            ExtractedClaim(
                subject="services/lore",
                predicate="states",
                object=fact,
                confidence="high",
                source_page_ids=[capture_id],
            )
        ],
    )


def candidate_for_capture(ledger: LedgerDB, capture_id: str) -> dict:
    return next(
        candidate
        for candidate in ledger.get_candidates(limit=10)
        if capture_id in candidate["source_capture_ids"]
    )


def test_get_unprocessed_captures_returns_only_draft_unprocessed(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    repo.upsert_page(
        "inbox/2026-05-10/reviewed",
        """---
title: Reviewed
kind: capture
visibility: internal
status: review
---

# Reviewed
""",
    )
    ledger = make_ledger(tmp_path)

    assert [capture.id for capture in get_unprocessed_captures(repo, ledger_db=ledger)] == [capture_id]

    result = extract_from_captures(repo, capture_ids=[capture_id], dry_run=False, ledger_db=ledger)
    assert result.source_capture_ids == [capture_id]
    assert get_unprocessed_captures(repo, ledger_db=ledger) == []


def test_extract_from_captures_dry_run_returns_results_without_storing(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    ledger = make_ledger(tmp_path)

    result = extract_from_captures(repo, capture_ids=[capture_id], dry_run=True, ledger_db=ledger)

    assert result.source_capture_ids == [capture_id]
    assert result.entities
    assert result.claims
    assert result.edges
    assert result.invalidations
    assert ledger.is_capture_extracted(capture_id) is False


def test_extract_from_captures_stores_to_ledger(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    ledger = make_ledger(tmp_path)

    result = extract_from_captures(repo, capture_ids=[capture_id], dry_run=False, ledger_db=ledger)

    assert ledger.is_capture_extracted(capture_id) is True
    assert ledger.get_batch(result.batch_id)["total_claims"] == 1
    assert ledger.get_candidates(candidate_type="claim")[0]["content_json"]["subject"] == "services/lore"


def test_compute_extraction_hash_is_deterministic_and_specific():
    first = compute_extraction_hash("Lore", "states", "Uses SQLite", ["services/lore", "inbox/a"])
    second = compute_extraction_hash(" lore ", "STATES", "Uses SQLite", ["inbox/a", "services/lore"])
    different = compute_extraction_hash("Lore", "states", "Uses Postgres", ["services/lore", "inbox/a"])

    assert first == second
    assert first != different
    assert len(first) == 64


def test_ledger_store_extraction_result_stores_all_candidate_types(tmp_path):
    ledger = make_ledger(tmp_path)
    result = ExtractionResult(
        batch_id="batch-1",
        processed_at="2026-05-10T00:00:00+00:00",
        source_capture_ids=["inbox/capture"],
        entities=[ExtractedEntity(name="Lore", entity_type="service", target_page_hint="services/lore")],
        claims=[
            ExtractedClaim(
                subject="services/lore",
                predicate="states",
                object="Lore stores candidates.",
                confidence="high",
                actor="nyx",
                lane="project",
                source_page_ids=["inbox/capture"],
            )
        ],
        edges=[
            ExtractedEdge(
                source_entity="services/lore",
                relationship_type="mentions",
                target_entity="services/workflow-engine",
                source_page_ids=["inbox/capture"],
            )
        ],
        invalidations=[
            ExtractedInvalidation(
                old_fact="Old fact",
                new_fact="New fact",
                reason="superseded",
                target_page_ids=["services/lore"],
            )
        ],
    )

    ledger.store_extraction_result(result)

    assert ledger.is_capture_extracted("inbox/capture") is True
    assert {candidate["candidate_type"] for candidate in ledger.get_candidates()} == {
        "entity",
        "claim",
        "edge",
        "invalidation",
    }
    assert len(ledger.get_candidates(candidate_type="claim", status="candidate")) == 1


def test_ledger_reset_extraction_for_specific_capture_ids(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.store_extraction_result(
        extraction_result("batch-1", "inbox/2026-05-10/one", "Lore stores reset state.")
    )
    ledger.store_extraction_result(
        extraction_result("batch-2", "inbox/2026-05-10/two", "Lore keeps other captures.")
    )
    first_candidate = candidate_for_capture(ledger, "inbox/2026-05-10/one")["candidate_id"]
    second_candidate = candidate_for_capture(ledger, "inbox/2026-05-10/two")["candidate_id"]
    ledger.activate_candidate(first_candidate)
    ledger.reject_candidate(second_candidate)

    reset_count = ledger.reset_extraction(capture_ids=["inbox/2026-05-10/one"])

    assert reset_count == 1
    assert ledger.is_capture_extracted("inbox/2026-05-10/one") is False
    assert ledger.is_capture_extracted("inbox/2026-05-10/two") is True
    assert candidate_for_capture(ledger, "inbox/2026-05-10/one")["status"] == "candidate"
    assert candidate_for_capture(ledger, "inbox/2026-05-10/two")["status"] == "rejected"


def test_ledger_reset_extraction_without_capture_ids_resets_all(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.store_extraction_result(
        extraction_result("batch-1", "inbox/2026-05-10/one", "Lore stores reset state.")
    )
    ledger.store_extraction_result(
        extraction_result("batch-2", "inbox/2026-05-10/two", "Lore keeps other captures.")
    )
    candidates = ledger.get_candidates(status="candidate", limit=10)
    ledger.activate_candidate(candidates[0]["candidate_id"])
    ledger.reject_candidate(candidates[1]["candidate_id"])

    reset_count = ledger.reset_extraction()

    assert reset_count == 2
    assert ledger.is_capture_extracted("inbox/2026-05-10/one") is False
    assert ledger.is_capture_extracted("inbox/2026-05-10/two") is False
    assert {candidate["status"] for candidate in ledger.get_candidates(limit=10)} == {"candidate"}


def test_extraction_request_validation():
    assert ExtractionRequest().dry_run is True
    assert ExtractionRequest(batch_size=50).batch_size == 50
    with pytest.raises(ValidationError):
        ExtractionRequest(batch_size=0)
    with pytest.raises(ValidationError):
        ExtractionRequest(batch_size=51)


def test_rule_based_extraction_preserves_provenance(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    ledger = make_ledger(tmp_path)

    result = extract_from_captures(repo, capture_ids=[capture_id], dry_run=True, ledger_db=ledger)

    entity_targets = {entity.target_page_hint for entity in result.entities}
    assert {"services/lore", "services/workflow-engine", "projects/example-project"}.issubset(entity_targets)
    claim = result.claims[0]
    assert claim.subject == "services/lore"
    assert claim.confidence == "high"
    assert claim.actor == "nyx"
    assert claim.lane == "project"
    assert claim.observed_at == "2026-05-10T00:00:00+00:00"
    assert claim.source_task == "flow_000375"
    assert claim.decision_id == "decisions/memory-policy"
    assert claim.trace_id == "trace-extraction-001"
    assert claim.policies_applied == ["L-MEM-03"]
    assert "inbox/2026-05-10/lore-ledger" in claim.source_page_ids
    assert any(edge.target_entity == "services/workflow-engine" for edge in result.edges)
    assert result.invalidations[0].old_fact == "Lore only stores captures as Markdown."


def test_llm_extraction_with_mock_client(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    repo.upsert_page(
        "inbox/test-capture",
        """---
title: Test Capture
kind: capture
visibility: internal
status: draft
---
The API Gateway routes requests to backends.
""",
    )
    mock_client = mock.MagicMock(spec=FallbackLLMClient)
    mock_client.extract_json.return_value = {
        "entities": [
            {"subject": "services/api", "name": "API Gateway", "entity_type": "service"},
        ],
        "claims": [
            {
                "subject": "services/api",
                "predicate": "routes",
                "object": "requests to backends",
                "confidence": "high",
                "source_page_ids": ["inbox/test-capture"],
            },
        ],
        "edges": [
            {"source": "services/api", "target": "services/backend", "edge_type": "depends_on"},
        ],
        "invalidations": [],
    }

    result = extract_from_captures(repo, dry_run=True, llm_client=mock_client, ledger_db=make_ledger(tmp_path))

    assert result.source_capture_ids == ["inbox/test-capture"]
    assert result.entities[0].target_page_hint == "services/api"
    assert result.claims[0].subject == "services/api"
    assert result.edges[0].relationship_type == "depends_on"
    assert mock_client.extract_json.call_count == 1


def test_deterministic_fallback_on_llm_failure(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    repo.upsert_page(
        "inbox/fallback-capture",
        """---
title: Fallback Capture
kind: capture
visibility: internal
status: draft
---
The [[services/auth]] module handles authentication.
""",
    )
    mock_client = mock.MagicMock(spec=FallbackLLMClient)
    mock_client.extract_json.side_effect = LLMError("Provider unavailable")

    result = extract_from_captures(repo, dry_run=True, llm_client=mock_client, ledger_db=make_ledger(tmp_path))

    assert result.source_capture_ids == ["inbox/fallback-capture"]
    assert any(entity.target_page_hint == "services/auth" for entity in result.entities)
    assert result.claims


def test_provider_none_forces_deterministic(client):
    client.app.state.llm_client = mock.MagicMock(spec=FallbackLLMClient)

    response = client.post(
        "/api/capture",
        json={
            "title": "Provider none capture",
            "observation": "Provider none references [[services/workflow-engine]].",
            "suggested_target_page": "services/lore",
        },
    )
    assert response.status_code == 201, response.text
    capture_id = response.json()["page"]["id"]

    extract = client.post(
        "/api/extraction/run",
        json={"capture_ids": [capture_id], "dry_run": True, "provider": "none"},
    )

    assert extract.status_code == 200, extract.text
    assert client.app.state.llm_client.extract_json.call_count == 0
    assert extract.json()["claims"][0]["subject"] == "services/lore"


def test_extraction_api_endpoints(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "Extraction API capture",
            "observation": "Lore extraction references [[services/workflow-engine]].",
            "confidence": "high",
            "actor": "nyx",
            "lane": "project",
            "suggested_target_page": "services/lore",
        },
    )
    assert response.status_code == 201, response.text
    capture_id = response.json()["page"]["id"]

    dry_run = client.post("/api/extraction/run", json={"capture_ids": [capture_id], "dry_run": True})
    assert dry_run.status_code == 200, dry_run.text
    dry_run_data = dry_run.json()
    assert dry_run_data["batch_id"]
    assert dry_run_data["source_capture_ids"] == [capture_id]
    # Rule-based extraction always produces at least one entity for suggested_target_page
    assert len(dry_run_data["entities"]) >= 1
    entity_targets = {e["target_page_hint"] for e in dry_run_data["entities"] if e.get("target_page_hint")}
    assert "services/lore" in entity_targets

    stored = client.post("/api/extraction/run", json={"capture_ids": [capture_id], "dry_run": False})
    assert stored.status_code == 200, stored.text
    batch_id = stored.json()["batch_id"]

    status = client.get("/api/extraction/status")
    assert status.status_code == 200
    assert status.json()["total_extracted"] >= 1

    batches = client.get("/api/extraction/batches")
    assert any(batch["batch_id"] == batch_id for batch in batches.json()["batches"])

    candidates = client.get("/api/extraction/candidates", params={"type": "claim", "status": "candidate"})
    assert candidates.status_code == 200
    assert candidates.json()["count"] >= 1


def test_extraction_reset_endpoint(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "Extraction reset capture",
            "observation": "Lore reset endpoint references [[services/workflow-engine]].",
            "confidence": "high",
            "suggested_target_page": "services/lore",
        },
    )
    assert response.status_code == 201, response.text
    capture_id = response.json()["page"]["id"]

    stored = client.post("/api/extraction/run", json={"capture_ids": [capture_id], "dry_run": False})
    assert stored.status_code == 200, stored.text
    assert client.get("/api/extraction/status").json()["total_extracted"] >= 1

    reset = client.post("/api/extraction/reset", json={"capture_ids": [capture_id]})
    assert reset.status_code == 200, reset.text
    assert reset.json() == {"reset_count": 1}

    rerun = client.post("/api/extraction/run", json={"capture_ids": [capture_id], "dry_run": False})
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["source_capture_ids"] == [capture_id]
