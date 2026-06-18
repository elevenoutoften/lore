from __future__ import annotations

import json
from unittest import mock

import pytest
from pydantic import ValidationError

from lore_app.cli import main
from lore_app.extraction import compute_extraction_hash, extract_from_captures, get_unprocessed_captures
from lore_app.ledger import LedgerDB
from lore_app.llm_extractor import llm_extract_capture
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


def invalid_llm_schema() -> dict:
    return {"entities": [], "claims": "not-a-list", "edges": [], "invalidations": []}


def valid_llm_response(capture_id: str, fact: str = "Lore stores structured candidates.") -> dict:
    return {
        "entities": [
            {"subject": "services/lore", "name": "Lore", "entity_type": "service"},
        ],
        "claims": [
            {
                "subject": "services/lore",
                "predicate": "states",
                "object": fact,
                "confidence": "high",
                "source_page_ids": [capture_id],
            },
        ],
        "edges": [],
        "invalidations": [],
    }


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
        candidate for candidate in ledger.get_candidates(limit=10) if capture_id in candidate["source_capture_ids"]
    )


def test_deterministic_extraction_splits_bullets_into_typed_claims(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = "inbox/2026-06-18/multi-fact"
    repo.upsert_page(
        capture_id,
        """---
title: Multi Fact Capture
kind: capture
visibility: internal
status: draft
suggested_target_page: services/lore
---

# Retrieval

- Lore uses SQLite for sparse recall.
- Lore requires an API key for dense embeddings.
- Lore supports deterministic cold starts.
""",
    )

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=make_ledger(tmp_path),
    )

    assert len(result.claims) == 3
    assert [claim.predicate for claim in result.claims] == ["uses", "requires", "supports"]
    assert {claim.section for claim in result.claims} == {"Retrieval"}


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
                section="Facts",
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
    [claim_candidate] = ledger.get_candidates(candidate_type="claim", status="candidate")
    assert claim_candidate["target_section"] == "Facts"


def test_ledger_reset_extraction_for_specific_capture_ids(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.store_extraction_result(extraction_result("batch-1", "inbox/2026-05-10/one", "Lore stores reset state."))
    ledger.store_extraction_result(extraction_result("batch-2", "inbox/2026-05-10/two", "Lore keeps other captures."))
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
    ledger.store_extraction_result(extraction_result("batch-1", "inbox/2026-05-10/one", "Lore stores reset state."))
    ledger.store_extraction_result(extraction_result("batch-2", "inbox/2026-05-10/two", "Lore keeps other captures."))
    candidates = ledger.get_candidates(status="candidate", limit=10)
    ledger.activate_candidate(candidates[0]["candidate_id"])
    ledger.reject_candidate(candidates[1]["candidate_id"])

    reset_count = ledger.reset_extraction()

    assert reset_count == 2
    assert ledger.is_capture_extracted("inbox/2026-05-10/one") is False
    assert ledger.is_capture_extracted("inbox/2026-05-10/two") is False
    assert {candidate["status"] for candidate in ledger.get_candidates(limit=10)} == {"candidate"}


def test_ledger_reset_extraction_delete_candidates_removes_candidates_and_logs(tmp_path):
    ledger = make_ledger(tmp_path)
    capture_id = "inbox/2026-05-10/delete-me"
    other_capture_id = "inbox/2026-05-10/keep-me"
    ledger.store_extraction_result(extraction_result("batch-1", capture_id, "Lore stores reset state."))
    ledger.store_extraction_result(extraction_result("batch-2", other_capture_id, "Lore keeps other captures."))
    deleted_candidate = candidate_for_capture(ledger, capture_id)
    kept_candidate = candidate_for_capture(ledger, other_capture_id)
    ledger.activate_candidate(deleted_candidate["candidate_id"])
    ledger.reject_candidate(kept_candidate["candidate_id"])

    reset_count = ledger.reset_extraction(capture_ids=[capture_id], delete_candidates=True)

    assert reset_count == 1
    assert ledger.is_capture_extracted(capture_id) is False
    assert ledger.is_capture_extracted(other_capture_id) is True
    assert ledger.get_candidates(capture_id=capture_id, limit=10) == []
    remaining_candidates = ledger.get_candidates(limit=10)
    assert len(remaining_candidates) == 1
    assert remaining_candidates[0]["source_capture_ids"] == [other_capture_id]
    assert remaining_candidates[0]["status"] == "rejected"


def test_reset_extraction_preserves_shared_candidate_provenance(tmp_path):
    ledger = make_ledger(tmp_path)
    capture_a = "inbox/2026-05-10/a"
    capture_b = "inbox/2026-05-10/b"

    ledger.store_extraction_result(extraction_result("batch-1", capture_a, "A fact"))
    ledger.store_extraction_result(extraction_result("batch-1", capture_b, "B fact"))
    ledger.store_extraction_result(
        ExtractionResult(
            batch_id="batch-1",
            processed_at="2026-05-10T00:00:00+00:00",
            source_capture_ids=[capture_a, capture_b],
            claims=[
                ExtractedClaim(
                    subject="services/lore",
                    predicate="states",
                    object="Shared fact from A and B",
                    confidence="high",
                    source_page_ids=[capture_a, capture_b],
                )
            ],
        )
    )

    shared_candidates = [
        candidate
        for candidate in ledger.get_candidates(limit=10)
        if set(candidate["source_capture_ids"]) == {capture_a, capture_b}
    ]
    assert len(shared_candidates) == 1

    reset_count = ledger.reset_extraction(capture_ids=[capture_a], delete_candidates=True)

    assert reset_count >= 1
    assert ledger.is_capture_extracted(capture_a) is False
    assert ledger.is_capture_extracted(capture_b) is True
    assert ledger.get_candidates(capture_id=capture_a, limit=10) == []

    remaining = ledger.get_candidates(limit=10)
    shared_remaining = [
        candidate for candidate in remaining if candidate["content_json"].get("object") == "Shared fact from A and B"
    ]
    assert len(shared_remaining) == 1
    assert shared_remaining[0]["source_capture_ids"] == [capture_b]
    assert [candidate for candidate in remaining if candidate["source_capture_ids"] == [capture_a]] == []


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


def test_schema_invalid_triggers_repair_retry(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    capture = repo.read_page(capture_id)
    mock_client = mock.MagicMock(spec=FallbackLLMClient)
    mock_client.extract_json.side_effect = [
        invalid_llm_schema(),
        valid_llm_response(capture_id, "Lore repairs invalid LLM schemas."),
    ]

    result = llm_extract_capture(capture, mock_client)

    assert mock_client.extract_json.call_count == 2
    repair_prompt = mock_client.extract_json.call_args_list[1].kwargs["user_prompt"]
    assert "previous response had schema validation errors" in repair_prompt
    assert result["claims"][0].object == "Lore repairs invalid LLM schemas."
    assert result["claims"][0].source_page_ids == [capture_id]


def test_schema_invalid_repair_also_fails(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    capture = repo.read_page(capture_id)
    mock_client = mock.MagicMock(spec=FallbackLLMClient)
    mock_client.extract_json.side_effect = [invalid_llm_schema(), invalid_llm_schema()]

    with pytest.raises(ValueError, match="claims must be a list"):
        llm_extract_capture(capture, mock_client)

    assert mock_client.extract_json.call_count == 2


def test_schema_invalid_escalates_to_glm(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    primary = mock.MagicMock()
    primary.extract_json.side_effect = [invalid_llm_schema(), invalid_llm_schema()]
    escalation = mock.MagicMock()
    escalation.extract_json.return_value = valid_llm_response(capture_id, "Escalation extracted valid schema.")
    llm_client = FallbackLLMClient(primary=primary, escalation=escalation)

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        llm_client=llm_client,
        ledger_db=make_ledger(tmp_path),
    )

    assert primary.extract_json.call_count == 2
    assert escalation.extract_json.call_count == 1
    assert result.claims[0].object == "Escalation extracted valid schema."
    assert result.claims[0].source_page_ids == [capture_id]


def test_schema_invalid_escalation_also_fails_falls_back(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    primary = mock.MagicMock()
    primary.extract_json.side_effect = [invalid_llm_schema(), invalid_llm_schema()]
    escalation = mock.MagicMock()
    escalation.extract_json.side_effect = [invalid_llm_schema(), invalid_llm_schema()]
    llm_client = FallbackLLMClient(primary=primary, escalation=escalation)

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        llm_client=llm_client,
        ledger_db=make_ledger(tmp_path),
    )

    assert primary.extract_json.call_count == 2
    assert escalation.extract_json.call_count == 2
    assert result.claims[0].subject == "services/lore"
    assert result.claims[0].object == "Lore stores structured extraction candidates in a SQLite ledger."
    assert result.claims[0].source_page_ids == [capture_id]


def test_llm_provenance_on_extracted_claims(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    repo.upsert_page(
        "inbox/test-llm-provenance",
        """---
title: Test LLM Provenance
kind: capture
visibility: internal
status: draft
observed_at: 2026-05-10T00:00:00+00:00
---
The API Gateway routes requests to backends.
""",
    )
    mock_client = mock.MagicMock(spec=FallbackLLMClient)
    mock_client.primary = mock.MagicMock()
    mock_client.primary.config.model = "glm-5.1"
    mock_client.extract_json.return_value = {
        "entities": [],
        "claims": [
            {
                "subject": "services/api",
                "predicate": "routes",
                "object": "requests to backends",
                "confidence": "high",
                "source_page_ids": ["inbox/test-llm-provenance"],
                "observed_at": "2025-01-01T00:00:00+00:00",
            },
        ],
        "edges": [],
        "invalidations": [],
        "_lore_meta": {
            "model": "glm-5.1",
            "usage": {"prompt_tokens": 42, "completion_tokens": 13},
        },
    }

    result = extract_from_captures(repo, dry_run=True, llm_client=mock_client, ledger_db=make_ledger(tmp_path))

    claim = result.claims[0]
    assert claim.model_version == "glm-5.1"
    assert claim.prompt_hash is not None
    assert len(claim.prompt_hash) == 16
    assert claim.token_usage == {"prompt": 42, "completion": 13}
    assert claim.observed_at is not None
    assert claim.observed_at != "2026-05-10T00:00:00+00:00"
    assert claim.observed_at == "2025-01-01T00:00:00+00:00"


def test_deterministic_claims_have_no_provenance(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    ledger = make_ledger(tmp_path)

    result = extract_from_captures(repo, capture_ids=[capture_id], dry_run=True, ledger_db=ledger)

    claim = result.claims[0]
    assert claim.model_version is None
    assert claim.prompt_hash is None
    assert claim.token_usage is None


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


def test_extraction_failure_inserts_deadletter(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    ledger = make_ledger(tmp_path)
    mock_client = mock.MagicMock(spec=FallbackLLMClient)

    with (
        mock.patch("lore_app.llm_extractor.llm_extract_capture", side_effect=LLMError("Provider unavailable")),
        mock.patch("lore_app.extraction._extract_capture", side_effect=RuntimeError("fallback failed")),
    ):
        result = extract_from_captures(
            repo,
            capture_ids=[capture_id],
            dry_run=False,
            llm_client=mock_client,
            ledger_db=ledger,
        )

    deadletters = ledger.list_deadletters(status="unresolved")
    assert result.source_capture_ids == []
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == capture_id
    assert deadletters[0]["provider"] == "fallback"
    assert deadletters[0]["failure_kind"] == "fallback_exhausted"
    assert "Provider unavailable" in str(deadletters[0]["failure_detail"])
    payload = json.loads(str(deadletters[0]["payload"]))
    assert payload["capture_id"] == capture_id
    assert ledger.is_capture_extracted(capture_id) is False
    assert capture_id in [page.id for page in get_unprocessed_captures(repo, ledger_db=ledger)]


def test_post_llm_failure_does_not_abort_batch(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    failed_capture = add_capture(repo, "inbox/2026-05-10/post-llm-broken")
    healthy_capture = add_capture(repo, "inbox/2026-05-10/post-llm-healthy")
    ledger = make_ledger(tmp_path)
    mock_client = mock.MagicMock(spec=FallbackLLMClient)

    def fake_llm_extract(capture, llm_client):
        del llm_client
        if capture.id == failed_capture:
            return {"entities": [], "claims": [object()], "edges": [], "invalidations": []}
        return {
            "entities": [],
            "claims": [
                ExtractedClaim(
                    subject="services/lore",
                    predicate="states",
                    object="Healthy post-LLM capture is recallable.",
                    confidence="high",
                    source_page_ids=[capture.id],
                )
            ],
            "edges": [],
            "invalidations": [],
        }

    with mock.patch("lore_app.llm_extractor.llm_extract_capture", side_effect=fake_llm_extract):
        result = extract_from_captures(
            repo,
            capture_ids=[failed_capture, healthy_capture],
            dry_run=False,
            llm_client=mock_client,
            ledger_db=ledger,
        )

    deadletters = ledger.list_deadletters(status="unresolved")
    assert result.source_capture_ids == [healthy_capture]
    assert ledger.is_capture_extracted(failed_capture) is False
    assert ledger.is_capture_extracted(healthy_capture) is True
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == failed_capture
    assert deadletters[0]["failure_kind"] == "processing_error"
    candidates = ledger.get_candidates(candidate_type="claim", capture_id=healthy_capture, limit=20)
    assert len(candidates) == 1
    assert candidates[0]["content_json"]["object"] == "Healthy post-LLM capture is recallable."


def test_retry_resolves_deadletter(tmp_path, monkeypatch, capsys):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    ledger = make_ledger(tmp_path)
    ledger.store_deadletter(
        capture_id=capture_id,
        provider="fallback",
        failure_kind="fallback_exhausted",
        failure_detail="llm=Provider unavailable; fallback=fallback failed",
        payload=json.dumps({"capture_id": capture_id}),
        batch_id="batch-deadletter",
    )

    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vectors.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))

    assert main(["extraction", "retry", "--limit", "10"]) == 0

    output = json.loads(capsys.readouterr().out)
    deadletters = ledger.list_deadletters(status="resolved")
    assert output == {"retried": 1, "resolved": 1}
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == capture_id
    assert deadletters[0]["status"] == "resolved"
    assert deadletters[0]["resolved_at"] is not None


def test_resolved_deadletter_not_retried(tmp_path, monkeypatch, capsys):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(repo)
    ledger = make_ledger(tmp_path)
    deadletter_id = ledger.store_deadletter(
        capture_id=capture_id,
        provider="fallback",
        failure_kind="fallback_exhausted",
        failure_detail="previous failure",
        payload=json.dumps({"capture_id": capture_id}),
        batch_id="batch-deadletter",
    )
    assert ledger.resolve_deadletter(deadletter_id) is True

    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_SEARCH_DB", str(tmp_path / "search.db"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_VECTOR_DB", str(tmp_path / "vectors.db"))
    monkeypatch.setenv("LORE_API_KEYS_DB", str(tmp_path / "api_keys.db"))

    assert main(["extraction", "retry", "--limit", "10"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {"retried": 0, "resolved": 0}
    assert ledger.list_deadletters(status="unresolved") == []
    assert len(ledger.list_deadletters(status="resolved")) == 1


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


def test_provider_escalation_forces_escalation_client(client):
    primary = mock.MagicMock()
    primary.extract_json.return_value = valid_llm_response("unused", "Primary should not run.")
    escalation = mock.MagicMock()
    client.app.state.llm_client = FallbackLLMClient(primary=primary, escalation=escalation)

    response = client.post(
        "/api/capture",
        json={
            "title": "Provider escalation capture",
            "observation": "Provider escalation references [[services/workflow-engine]].",
            "suggested_target_page": "services/lore",
        },
    )
    assert response.status_code == 201, response.text
    capture_id = response.json()["page"]["id"]
    escalation.extract_json.return_value = valid_llm_response(capture_id, "Escalation was forced.")

    extract = client.post(
        "/api/extraction/run",
        json={"capture_ids": [capture_id], "dry_run": True, "provider": "escalation"},
    )

    assert extract.status_code == 200, extract.text
    assert primary.extract_json.call_count == 0
    assert escalation.extract_json.call_count == 1
    assert extract.json()["claims"][0]["object"] == "Escalation was forced."


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


def test_extraction_deadletter_retry_endpoint_returns_candidates(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "Extraction retry capture",
            "observation": "Lore retry endpoint references [[services/workflow-engine]].",
            "confidence": "high",
            "suggested_target_page": "services/lore",
        },
    )
    assert response.status_code == 201, response.text
    capture_id = response.json()["page"]["id"]
    ledger = client.app.state.ledger_db
    deadletter_id = ledger.store_deadletter(
        capture_id=capture_id,
        provider="fallback",
        failure_kind="fallback_exhausted",
        failure_detail="previous failure",
        payload=json.dumps({"capture_id": capture_id}),
        batch_id="batch-deadletter",
    )

    retry = client.post(f"/api/extraction/deadletters/{deadletter_id}/retry")

    assert retry.status_code == 200, retry.text
    payload = retry.json()
    assert payload["deadletter_id"] == deadletter_id
    assert payload["capture_id"] == capture_id
    assert payload["retried"] is True
    assert payload["resolved"] is True
    assert payload["candidates"] > 0
    assert payload["source_capture_ids"] == [capture_id]
