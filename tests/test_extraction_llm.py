from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import lore_app.extraction as extraction_module
import lore_app.llm_extractor as llm_extractor_module
from lore_app.cli import main as cli_main
from lore_app.extraction import extract_from_captures
from lore_app.ledger import LedgerDB
from lore_app.llm_provider import FallbackLLMClient, LLMError, NoLlmClient
from lore_app.repository import LoreRepository


def make_ledger(tmp_path) -> LedgerDB:
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    return ledger


def add_capture(
    repo: LoreRepository,
    page_id: str,
    *,
    title: str,
    summary: str,
    suggested_target_page: str,
    body: str,
    confidence: str = "high",
    actor: str = "nyx",
    lane: str = "project",
    observed_at: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    source_task: str | None = None,
    decision_id: str | None = None,
    trace_id: str | None = None,
) -> str:
    frontmatter_lines = [
        "---",
        f"title: {title}",
        "kind: capture",
        "visibility: internal",
        "status: draft",
        f"summary: {summary}",
        f"confidence: {confidence}",
        f"actor: {actor}",
        f"lane: {lane}",
        f"suggested_target_page: {suggested_target_page}",
    ]
    if observed_at is not None:
        frontmatter_lines.append(f"observed_at: {observed_at}")
    if valid_from is not None:
        frontmatter_lines.append(f"valid_from: {valid_from}")
    if valid_until is not None:
        frontmatter_lines.append(f"valid_until: {valid_until}")
    if source_task is not None:
        frontmatter_lines.append(f"source_task: {source_task}")
    if decision_id is not None:
        frontmatter_lines.append(f"decision_id: {decision_id}")
    if trace_id is not None:
        frontmatter_lines.append(f"trace_id: {trace_id}")
    frontmatter_lines.append("---")
    repo.upsert_page(
        page_id,
        "\n".join(frontmatter_lines) + f"\n\n{body}\n",
    )
    return page_id


@dataclass
class FakeLlmResponse:
    payload: dict | object
    usage: dict[str, int] | None = None
    model: str | None = None


class FakeLlmClient:
    def __init__(self, modes: dict[str, str], responses: dict[str, FakeLlmResponse | dict]):
        self.modes = modes
        self.responses = responses
        self.primary = SimpleNamespace(config=SimpleNamespace(model="fake-test-model"))

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> dict:
        del system_prompt, temperature, model
        page_id = _page_id_from_prompt(user_prompt)
        mode = self.modes.get(page_id, "success")
        if mode == "error":
            raise LLMError(f"fake llm failure for {page_id}")
        if mode == "timeout":
            raise LLMError(f"fake timeout while extracting {page_id}")
        if mode == "invalid":
            return "not-json"  # type: ignore[return-value]

        response = self.responses.get(page_id, {"entities": [], "claims": [], "edges": [], "invalidations": []})
        if isinstance(response, FakeLlmResponse):
            payload = dict(response.payload)
            payload["_lore_meta"] = {
                "usage": response.usage or {"prompt_tokens": 0, "completion_tokens": 0},
                "model": response.model or self.primary.config.model,
            }
            return payload
        return response


@pytest.fixture()
def fake_llm_client():
    def _build(
        *,
        modes: dict[str, str] | None = None,
        responses: dict[str, FakeLlmResponse | dict] | None = None,
    ) -> FakeLlmClient:
        return FakeLlmClient(modes or {}, responses or {})

    return _build


def _page_id_from_prompt(user_prompt: str) -> str:
    match = re.search(r"^Page ID:\s*(.+)$", user_prompt, re.MULTILINE)
    assert match, f"missing page id in prompt: {user_prompt!r}"
    return match.group(1).strip()


def quality_drop_response(capture_id: str) -> dict:
    return {
        "entities": [
            None,
            None,
            {"subject": "services/lore", "name": "Lore", "entity_type": "service"},
        ],
        "claims": [
            {
                "subject": "services/lore",
                "predicate": "states",
                "object": "Lore has one valid claim.",
                "confidence": "high",
                "source_page_ids": [capture_id],
            },
        ],
        "edges": [],
        "invalidations": [],
    }


class SequencedLlmClient:
    def __init__(self, responses: list[dict | object], *, model: str = "sequenced-test-model"):
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.primary = SimpleNamespace(config=SimpleNamespace(model=model))

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> dict:
        del system_prompt, temperature, model
        self.prompts.append(user_prompt)
        assert self._responses, "no more queued LLM responses"
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]

    def close(self) -> None:
        pass


def test_quality_drops_trigger_value_error():
    raw = {
        "entities": [
            None,
            None,
            {"subject": "services/lore", "name": "Lore", "entity_type": "service"},
        ],
        "claims": [],
        "edges": [],
        "invalidations": [],
    }

    with pytest.raises(ValueError, match="dropped"):
        llm_extractor_module._validate_llm_output(raw, "inbox/quality-drops", "Quality Drops")


def test_all_items_dropped_triggers_value_error():
    raw = {
        "entities": [None],
        "claims": [None],
        "edges": [None],
        "invalidations": [None],
    }

    with pytest.raises(ValueError, match="zero valid items"):
        llm_extractor_module._validate_llm_output(raw, "inbox/all-dropped", "All Dropped")


def test_low_confidence_triggers_value_error():
    raw = {
        "entities": [],
        "claims": [
            {
                "subject": "services/lore",
                "predicate": "states",
                "object": f"Low confidence claim {index}.",
                "confidence": "low",
                "source_page_ids": ["inbox/low-confidence"],
            }
            for index in range(5)
        ],
        "edges": [],
        "invalidations": [],
    }

    with pytest.raises(ValueError, match="low confidence"):
        llm_extractor_module._validate_llm_output(raw, "inbox/low-confidence", "Low Confidence")


def test_deterministic_only_extraction_has_no_llm_provenance(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/deterministic-only",
        title="Deterministic Only",
        summary="Lore references the workflow engine.",
        suggested_target_page="services/lore",
        body="Lore references [[Workflow Engine|services/workflow-engine]].",
    )

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=None,
    )

    assert result.source_capture_ids == [capture_id]
    assert {entity.target_page_hint for entity in result.entities} == {
        "services/lore",
        "services/workflow-engine",
    }
    claim = result.claims[0]
    assert claim.subject == "services/lore"
    assert claim.section is None
    assert claim.model_version is None
    assert claim.prompt_hash is None
    assert claim.token_usage is None


def test_llm_success_populates_provenance_and_merges_results(tmp_path, monkeypatch, fake_llm_client):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    first_capture = add_capture(
        repo,
        "inbox/2026-05-26/llm-success-a",
        title="LLM Success A",
        summary="Lore uses the workflow engine.",
        suggested_target_page="services/lore",
        body="Lore uses [[Workflow Engine|services/workflow-engine]].",
    )
    second_capture = add_capture(
        repo,
        "inbox/2026-05-26/llm-success-b",
        title="LLM Success B",
        summary="Lore still uses the workflow engine.",
        suggested_target_page="services/lore",
        body="Lore still uses [[Workflow Engine|services/workflow-engine]].",
    )
    observed_at = datetime(2026, 5, 26, 12, 34, 56, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed_at if tz is None else observed_at.astimezone(tz)

    monkeypatch.setattr(extraction_module, "datetime", FixedDateTime)
    llm_client = fake_llm_client(
        responses={
            first_capture: FakeLlmResponse(
                payload={
                    "entities": [
                        {
                            "subject": "services/lore",
                            "name": "Lore",
                            "entity_type": "service",
                        },
                        {
                            "subject": "services/workflow-engine",
                            "name": "Workflow Engine",
                            "entity_type": "service",
                        },
                    ],
                    "claims": [
                        {
                            "subject": "services/lore",
                            "predicate": "depends_on",
                            "object": "Workflow Engine",
                            "confidence": "high",
                            "section": "Architecture",
                            "source_page_ids": [first_capture],
                        }
                    ],
                    "edges": [
                        {
                            "source": "services/lore",
                            "target": "services/workflow-engine",
                            "edge_type": "depends_on",
                            "source_page_ids": [first_capture],
                        }
                    ],
                    "invalidations": [],
                },
                usage={"prompt_tokens": 21, "completion_tokens": 13},
            ),
            second_capture: FakeLlmResponse(
                payload={
                    "entities": [
                        {
                            "subject": "services/workflow-engine",
                            "name": "Workflow Engine",
                            "entity_type": "service",
                        }
                    ],
                    "claims": [
                        {
                            "subject": "services/lore",
                            "predicate": "uses",
                            "object": "Workflow Engine",
                            "confidence": "medium",
                            "source_page_ids": [second_capture],
                        }
                    ],
                    "edges": [
                        {
                            "source": "services/lore",
                            "target": "services/workflow-engine",
                            "edge_type": "depends_on",
                            "source_page_ids": [first_capture],
                        }
                    ],
                    "invalidations": [],
                },
                usage={"prompt_tokens": 8, "completion_tokens": 5},
            ),
        }
    )

    result = extract_from_captures(
        repo,
        capture_ids=[first_capture, second_capture],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [first_capture, second_capture]
    assert {(entity.name, entity.target_page_hint) for entity in result.entities} == {
        ("Lore", "services/lore"),
        ("Workflow Engine", "services/workflow-engine"),
    }
    assert len(result.edges) == 1
    assert result.edges[0].relationship_type == "depends_on"
    assert len(result.claims) == 2
    for claim in result.claims:
        assert claim.model_version == "fake-test-model"
        assert claim.prompt_hash is not None
        assert claim.token_usage is not None
        assert claim.observed_at == observed_at.isoformat()
    sections_by_claim = {(claim.predicate, claim.object): claim.section for claim in result.claims}
    assert sections_by_claim[("depends_on", "Workflow Engine")] == "Architecture"


def test_llm_successful_extraction(tmp_path, monkeypatch, fake_llm_client):
    """LLM extraction returns valid results with correct provenance."""
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/llm-successful-extraction",
        title="Successful Extraction",
        summary="Lore depends on auth and retires old sync claims.",
        suggested_target_page="services/lore",
        body="Lore depends on [[Auth Service|services/auth]].",
    )
    observed_at = datetime(2026, 5, 26, 15, 0, 0, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed_at if tz is None else observed_at.astimezone(tz)

    monkeypatch.setattr(extraction_module, "datetime", FixedDateTime)
    llm_client = fake_llm_client(
        responses={
            capture_id: FakeLlmResponse(
                payload={
                    "entities": [
                        {"subject": "services/lore", "name": "Lore", "entity_type": "service"},
                        {"subject": "services/auth", "name": "Auth Service", "entity_type": "service"},
                    ],
                    "claims": [
                        {
                            "subject": "services/lore",
                            "predicate": "depends_on",
                            "object": "Auth Service",
                            "confidence": "high",
                            "actor": "extractor-bot",
                            "lane": "ops",
                            "source_page_ids": [capture_id],
                            "section": "Architecture",
                        }
                    ],
                    "edges": [
                        {
                            "source": "services/lore",
                            "target": "services/auth",
                            "edge_type": "depends_on",
                            "source_page_ids": [capture_id],
                        }
                    ],
                    "invalidations": [
                        {
                            "target_claim": "services/lore syncs directly to auth",
                            "new_fact": "services/lore depends on Auth Service",
                            "reason": "Architecture was updated.",
                            "target_page_ids": [capture_id],
                        }
                    ],
                },
                usage={"prompt_tokens": 17, "completion_tokens": 11},
            )
        }
    )

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [capture_id]
    assert {(entity.name, entity.target_page_hint) for entity in result.entities} == {
        ("Lore", "services/lore"),
        ("Auth Service", "services/auth"),
    }
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.subject == "services/lore"
    assert claim.predicate == "depends_on"
    assert claim.object == "Auth Service"
    assert claim.source_page_ids == [capture_id]
    assert claim.actor == "extractor-bot"
    assert claim.lane == "ops"
    assert claim.observed_at == observed_at.isoformat()
    assert claim.model_version == "fake-test-model"
    assert claim.prompt_hash is not None
    assert claim.token_usage == {"prompt": 17, "completion": 11}
    assert len(result.edges) == 1
    assert result.edges[0].relationship_type == "depends_on"
    assert result.edges[0].source_page_ids == [capture_id]
    assert len(result.invalidations) == 1
    assert result.invalidations[0].old_fact == "services/lore syncs directly to auth"
    assert result.invalidations[0].reason == "Architecture was updated."
    assert result.invalidations[0].target_page_ids == [capture_id]


def test_llm_failure_records_deadletter_even_when_deterministic_succeeds(tmp_path, fake_llm_client):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/llm-fallback",
        title="LLM Fallback",
        summary="Lore mentions auth.",
        suggested_target_page="services/lore",
        body="Lore mentions [[services/auth]].",
    )
    llm_client = fake_llm_client(modes={capture_id: "error"})

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [capture_id]
    assert any(entity.target_page_hint == "services/auth" for entity in result.entities)
    claim = result.claims[0]
    assert claim.subject == "services/lore"
    assert claim.model_version is None
    assert claim.prompt_hash is None
    assert claim.token_usage is None
    deadletters = ledger.list_deadletters(status="unresolved")
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == capture_id
    assert deadletters[0]["provider"] == "fake-test-model"
    assert deadletters[0]["failure_kind"] == "llm_error"
    assert f"fake llm failure for {capture_id}" in str(deadletters[0]["failure_detail"])


def test_llm_error_fallback_preserves_provenance(tmp_path, fake_llm_client):
    """Deterministic fallback preserves capture provenance when LLM fails."""
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/fallback-provenance",
        title="Test Capture",
        summary="Lore depends on [[services/auth]].",
        suggested_target_page="services/lore",
        observed_at="2026-05-26T00:00:00Z",
        valid_from="2026-05-01",
        actor="test-agent",
        lane="extraction",
        body="Some content [[services/auth]].",
    )
    llm_client = fake_llm_client(modes={capture_id: "error"})

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [capture_id]
    assert any(entity.target_page_hint == "services/auth" for entity in result.entities)
    assert any(edge.target_entity == "services/auth" for edge in result.edges)
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.subject == "services/lore"
    assert claim.predicate == "states"
    assert claim.object == "Lore depends on [[services/auth]]."
    assert claim.observed_at == "2026-05-26T00:00:00Z"
    assert claim.valid_from == "2026-05-01"
    assert claim.actor == "test-agent"
    assert claim.lane == "extraction"
    assert claim.source_page_ids == [capture_id]
    assert claim.model_version is None
    assert claim.prompt_hash is None
    assert claim.token_usage is None


def test_schema_invalid_records_deadletter_on_deterministic_success(tmp_path, fake_llm_client):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/schema-invalid",
        title="Schema Invalid",
        summary="Lore mentions auth.",
        suggested_target_page="services/lore",
        body="Lore mentions [[services/auth]].",
    )
    llm_client = fake_llm_client(modes={capture_id: "invalid"})

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [capture_id]
    assert any(entity.target_page_hint == "services/auth" for entity in result.entities)
    deadletters = ledger.list_deadletters(status="unresolved")
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == capture_id
    assert deadletters[0]["provider"] == "fake-test-model"
    assert deadletters[0]["failure_kind"] == "schema_invalid"
    assert "expected object, got str" in str(deadletters[0]["failure_detail"])


def test_llm_timeout_records_deadletter_with_kind_timeout(tmp_path, fake_llm_client):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/llm-timeout",
        title="LLM Timeout",
        summary="Lore mentions auth.",
        suggested_target_page="services/lore",
        body="Lore mentions [[services/auth]].",
    )
    llm_client = fake_llm_client(modes={capture_id: "timeout"})

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [capture_id]
    assert any(entity.target_page_hint == "services/auth" for entity in result.entities)
    deadletters = ledger.list_deadletters(status="unresolved")
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == capture_id
    assert deadletters[0]["provider"] == "fake-test-model"
    assert deadletters[0]["failure_kind"] == "timeout"


def test_repair_retry_on_schema_invalid(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/repair-retry",
        title="Repair Retry",
        summary="Lore uses auth.",
        suggested_target_page="services/lore",
        body="Lore uses [[Auth Service|services/auth]].",
    )
    capture = repo.read_page(capture_id)
    assert capture is not None
    llm_client = SequencedLlmClient(
        responses=[
            {"entities": [], "claims": "not-a-list", "edges": [], "invalidations": []},
            {
                "entities": [
                    {"subject": "services/lore", "name": "Lore", "entity_type": "service"},
                ],
                "claims": [
                    {
                        "subject": "services/lore",
                        "predicate": "uses",
                        "object": "Auth Service",
                        "confidence": "high",
                        "source_page_ids": [capture_id],
                    }
                ],
                "edges": [],
                "invalidations": [],
            },
        ],
        model="repair-model",
    )

    result = llm_extractor_module.llm_extract_capture(capture, llm_client)

    assert len(llm_client.prompts) == 2
    assert "IMPORTANT: Your previous response had schema validation errors." in llm_client.prompts[1]
    repair_prompt = llm_client.prompts[1]
    assert "predicate" in repair_prompt
    assert "object" in repair_prompt
    assert "entity_type" in repair_prompt
    assert "target_claim" in repair_prompt
    assert "reason" in repair_prompt
    assert "edge_type" in repair_prompt
    assert "kind" not in repair_prompt or "entity_type" in repair_prompt
    assert "invalidation_reason" not in repair_prompt
    assert "original_claim" not in repair_prompt
    assert len(result["claims"]) == 1
    assert result["claims"][0].predicate == "uses"
    assert result["claims"][0].model_version == "repair-model"
    assert result["claims"][0].prompt_hash is not None


def test_quality_error_triggers_repair_retry(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/quality-repair-retry",
        title="Quality Repair Retry",
        summary="Lore uses auth.",
        suggested_target_page="services/lore",
        body="Lore uses [[Auth Service|services/auth]].",
    )
    capture = repo.read_page(capture_id)
    assert capture is not None
    llm_client = SequencedLlmClient(
        responses=[
            quality_drop_response(capture_id),
            {
                "entities": [
                    {"subject": "services/lore", "name": "Lore", "entity_type": "service"},
                ],
                "claims": [
                    {
                        "subject": "services/lore",
                        "predicate": "uses",
                        "object": "Auth Service",
                        "confidence": "high",
                        "source_page_ids": [capture_id],
                    }
                ],
                "edges": [],
                "invalidations": [],
            },
        ],
        model="quality-repair-model",
    )

    result = llm_extractor_module.llm_extract_capture(capture, llm_client)

    assert len(llm_client.prompts) == 2
    assert "IMPORTANT: Your previous response had schema validation errors." in llm_client.prompts[1]
    assert len(result["entities"]) == 1
    assert result["claims"][0].predicate == "uses"
    assert result["claims"][0].model_version == "quality-repair-model"
    assert result["dropped"] == {"entities": 0, "claims": 0, "edges": 0, "invalidations": 0}
    assert result["total"] == {"entities": 1, "claims": 1, "edges": 0, "invalidations": 0}


def test_escalation_on_schema_invalid_failure(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/escalation-schema-invalid",
        title="Escalation Capture",
        summary="Lore depends on auth.",
        suggested_target_page="services/lore",
        body="Lore depends on [[Auth Service|services/auth]].",
    )
    primary = SequencedLlmClient(
        responses=[
            {"entities": [], "claims": "not-a-list", "edges": [], "invalidations": []},
            {"entities": [], "claims": "still-not-a-list", "edges": [], "invalidations": []},
        ],
        model="primary-model",
    )
    escalation = SequencedLlmClient(
        responses=[
            {
                "entities": [
                    {"subject": "services/lore", "name": "Lore", "entity_type": "service"},
                    {"subject": "services/auth", "name": "Auth Service", "entity_type": "service"},
                ],
                "claims": [
                    {
                        "subject": "services/lore",
                        "predicate": "depends_on",
                        "object": "Auth Service",
                        "confidence": "high",
                        "source_page_ids": [capture_id],
                    }
                ],
                "edges": [
                    {
                        "source": "services/lore",
                        "target": "services/auth",
                        "edge_type": "depends_on",
                        "source_page_ids": [capture_id],
                    }
                ],
                "invalidations": [],
            }
        ],
        model="escalation-model",
    )
    llm_client = FallbackLLMClient(primary=primary, escalation=escalation)

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [capture_id]
    assert len(primary.prompts) == 2
    assert len(escalation.prompts) == 1
    assert len(result.claims) == 1
    assert result.claims[0].predicate == "depends_on"
    assert result.claims[0].model_version == "escalation-model"
    assert ledger.list_deadletters(status="unresolved") == []


def test_quality_error_triggers_escalation_via_extraction(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/quality-escalation",
        title="Quality Escalation",
        summary="Lore depends on auth.",
        suggested_target_page="services/lore",
        body="Lore depends on [[Auth Service|services/auth]].",
    )
    primary = SequencedLlmClient(
        responses=[
            quality_drop_response(capture_id),
            quality_drop_response(capture_id),
        ],
        model="primary-quality-model",
    )
    escalation = SequencedLlmClient(
        responses=[
            quality_drop_response(capture_id),
            quality_drop_response(capture_id),
        ],
        model="escalation-quality-model",
    )
    llm_client = FallbackLLMClient(primary=primary, escalation=escalation)

    result = extract_from_captures(
        repo,
        capture_ids=[capture_id],
        dry_run=True,
        ledger_db=ledger,
        llm_client=llm_client,
    )

    assert result.source_capture_ids == [capture_id]
    assert len(primary.prompts) == 2
    assert len(escalation.prompts) == 2
    assert result.claims[0].model_version is None
    assert any(entity.target_page_hint == "services/auth" for entity in result.entities)
    deadletters = ledger.list_deadletters(status="unresolved")
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == capture_id
    assert deadletters[0]["failure_kind"] == "schema_invalid"
    assert "dropped" in str(deadletters[0]["failure_detail"])


def test_both_fail_records_deadletter_with_kind_fallback_exhausted(tmp_path, fake_llm_client):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    failed_capture = add_capture(
        repo,
        "inbox/2026-05-26/llm-deadletter",
        title="Broken Capture",
        summary="This capture will fail.",
        suggested_target_page="services/lore",
        body="Lore mentions [[services/auth]].",
    )
    healthy_capture = add_capture(
        repo,
        "inbox/2026-05-26/llm-healthy",
        title="Healthy Capture",
        summary="This capture should still process.",
        suggested_target_page="services/lore",
        body="Lore depends on the workflow engine.",
    )
    llm_client = fake_llm_client(
        modes={failed_capture: "error"},
        responses={
            healthy_capture: FakeLlmResponse(
                payload={
                    "entities": [
                        {
                            "subject": "services/lore",
                            "name": "Lore",
                            "entity_type": "service",
                        }
                    ],
                    "claims": [
                        {
                            "subject": "services/lore",
                            "predicate": "states",
                            "object": "Healthy capture processed.",
                            "confidence": "high",
                            "source_page_ids": [healthy_capture],
                        }
                    ],
                    "edges": [],
                    "invalidations": [],
                }
            )
        },
    )
    original_extract_capture = extraction_module._extract_capture

    def fail_only_for_broken(repo_arg, capture_arg):
        if capture_arg.id == failed_capture:
            raise RuntimeError("fallback failed")
        return original_extract_capture(repo_arg, capture_arg)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(extraction_module, "_extract_capture", fail_only_for_broken)
        result = extract_from_captures(
            repo,
            capture_ids=[failed_capture, healthy_capture],
            dry_run=True,
            ledger_db=ledger,
            llm_client=llm_client,
        )

    deadletters = ledger.list_deadletters(status="unresolved")
    assert result.source_capture_ids == [failed_capture, healthy_capture]
    assert len(result.claims) == 1
    assert result.claims[0].object == "Healthy capture processed."
    assert len(deadletters) == 1
    assert deadletters[0]["capture_id"] == failed_capture
    assert deadletters[0]["provider"] == "fallback"
    assert deadletters[0]["failure_kind"] == "fallback_exhausted"
    assert failed_capture in result.source_capture_ids


def test_retry_cli_resolves_deadletter(tmp_path, monkeypatch, capsys):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/retry-cli",
        title="Retry CLI",
        summary="Lore mentions auth.",
        suggested_target_page="services/lore",
        body="Lore mentions [[services/auth]].",
    )
    deadletter_id = ledger.store_deadletter(
        capture_id=capture_id,
        provider="fake-test-model",
        failure_kind="llm_error",
        failure_detail="boom",
        payload="{}",
        batch_id="batch-retry",
    )
    llm_client = object()
    calls: list[dict[str, object]] = []

    def fake_extract_from_captures(repo_arg, capture_ids=None, batch_size=10, dry_run=True, *, ledger_db=None, llm_client=None):
        del repo_arg, batch_size, ledger_db
        calls.append(
            {
                "capture_ids": list(capture_ids or []),
                "dry_run": dry_run,
                "llm_client": llm_client,
            }
        )
        return SimpleNamespace(source_capture_ids=list(capture_ids or []))

    monkeypatch.setattr(extraction_module, "extract_from_captures", fake_extract_from_captures)
    monkeypatch.setattr("lore_app.llm_provider.build_llm_client", lambda *, config: llm_client)
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))

    assert cli_main(["extraction", "retry", "--limit", "1"]) == 0
    first_output = capsys.readouterr().out
    assert '"retried": 1' in first_output
    assert '"resolved": 1' in first_output
    resolved_deadletters = ledger.list_deadletters(status="resolved")
    assert len(resolved_deadletters) == 1
    assert resolved_deadletters[0]["deadletter_id"] == deadletter_id
    assert resolved_deadletters[0]["resolved_by"] == "llm"
    assert resolved_deadletters[0]["retry_count"] == 1
    assert resolved_deadletters[0]["attempted_at"] is not None
    assert resolved_deadletters[0]["last_retry_at"] is not None
    assert ledger.list_deadletters(status="unresolved") == []
    assert calls == [{"capture_ids": [capture_id], "dry_run": False, "llm_client": llm_client}]

    assert cli_main(["extraction", "retry", "--limit", "1"]) == 0
    second_output = capsys.readouterr().out
    assert '"retried": 0' in second_output
    assert '"resolved": 0' in second_output
    assert calls == [{"capture_ids": [capture_id], "dry_run": False, "llm_client": llm_client}]


def test_retry_cli_marks_no_provider_resolution_as_deterministic(tmp_path, monkeypatch, capsys):
    repo = LoreRepository(tmp_path / "pages")
    ledger = make_ledger(tmp_path)
    capture_id = add_capture(
        repo,
        "inbox/2026-05-26/retry-cli-no-provider",
        title="Retry CLI No Provider",
        summary="Lore mentions auth.",
        suggested_target_page="services/lore",
        body="Lore mentions [[services/auth]].",
    )
    deadletter_id = ledger.store_deadletter(
        capture_id=capture_id,
        provider="fake-test-model",
        failure_kind="llm_error",
        failure_detail="boom",
        payload="{}",
        batch_id="batch-retry",
    )

    def fake_extract_from_captures(repo_arg, capture_ids=None, batch_size=10, dry_run=True, *, ledger_db=None, llm_client=None):
        del repo_arg, batch_size, dry_run, ledger_db
        assert isinstance(llm_client, NoLlmClient)
        return SimpleNamespace(source_capture_ids=list(capture_ids or []))

    monkeypatch.setattr(extraction_module, "extract_from_captures", fake_extract_from_captures)
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("LORE_LLM_PROVIDER", "none")

    assert cli_main(["extraction", "retry", "--limit", "1"]) == 0
    output = capsys.readouterr().out
    assert '"retried": 1' in output
    assert '"resolved": 1' in output
    resolved_deadletters = ledger.list_deadletters(status="resolved")
    assert len(resolved_deadletters) == 1
    assert resolved_deadletters[0]["deadletter_id"] == deadletter_id
    assert resolved_deadletters[0]["resolved_by"] == "deterministic"
