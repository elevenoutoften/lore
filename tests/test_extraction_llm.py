from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import lore_app.extraction as extraction_module
from lore_app.extraction import extract_from_captures
from lore_app.ledger import LedgerDB
from lore_app.llm_provider import LLMError
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
) -> str:
    repo.upsert_page(
        page_id,
        textwrap.dedent(
            f"""\
            ---
            title: {title}
            kind: capture
            visibility: internal
            status: draft
            summary: {summary}
            confidence: high
            actor: nyx
            lane: project
            suggested_target_page: {suggested_target_page}
            ---

            {body}
            """
        ),
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
