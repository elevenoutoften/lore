#!/usr/bin/env python3
"""Extraction evaluation runner for Lore.

Usage:
    # CI mode (no live API calls):
    python tests/eval_extraction.py --ci

    # Live mode against specific provider:
    LORE_LLM_API_KEY=... python tests/eval_extraction.py --live --provider qwen3.6-plus
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lore_app.extraction import extract_from_captures
from lore_app.ledger import LedgerDB
from lore_app.llm_provider import FallbackLLMClient, LLMClient, LLMProviderConfig
from lore_app.repository import LoreRepository
from lore_app.schemas import ExtractedClaim
from tests.eval_extraction_fixtures import EVAL_FIXTURES

SUPPORTED_PROVIDERS = {"qwen3.6-plus", "glm-5.1", "gemma-4"}


class RecordedLLMClient:
    """CI-safe provider that returns deterministic fixture responses."""

    def __init__(self, fixtures: list[dict[str, Any]]):
        self.responses = {fixture["name"]: _fake_response(fixture) for fixture in fixtures}
        self.last_raw: dict[str, Any] | None = None
        self.last_fallback_used = False

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        del system_prompt, temperature, model
        fixture_name = _fixture_name_from_prompt(user_prompt)
        self.last_raw = self.responses[fixture_name]
        self.last_fallback_used = False
        return self.last_raw

    def close(self) -> None:
        return None


class RecordingFallbackLLMClient(FallbackLLMClient):
    def __init__(self, primary: Any, escalation: Any | None = None, fallback_fn: Any | None = None):
        super().__init__(primary=primary, escalation=escalation, fallback_fn=fallback_fn)
        self.last_raw: dict[str, Any] | None = None
        self.last_fallback_used = False

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        if hasattr(self.primary, "last_raw"):
            self.primary.last_raw = None
        raw = super().extract_json(
            system_prompt,
            user_prompt,
            temperature=temperature,
            model=model,
        )
        self.last_raw = raw
        self.last_fallback_used = bool(getattr(self.primary, "last_fallback_used", False))
        return raw


def run_eval(mode: str = "ci", provider: str | None = None) -> list[dict[str, Any]]:
    if mode not in {"ci", "live"}:
        raise ValueError("mode must be 'ci' or 'live'")
    if provider is not None and provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")

    llm_client = _build_eval_client(mode, provider)
    results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="lore-extraction-eval-") as tmp:
            for fixture in EVAL_FIXTURES:
                repo = LoreRepository(Path(tmp) / fixture["name"] / "pages")
                ledger = LedgerDB(Path(tmp) / fixture["name"] / "ledger.db")
                ledger.initialize()
                try:
                    capture_id = _write_capture(repo, fixture)
                    raw_before = getattr(llm_client, "last_raw", None)
                    result = extract_from_captures(
                        repo,
                        capture_ids=[capture_id],
                        dry_run=True,
                        ledger_db=ledger,
                        llm_client=llm_client,
                    )
                    raw = getattr(llm_client, "last_raw", None)
                    json_valid = isinstance(raw, dict) if raw is not raw_before else False
                    results.append(_metrics_for_fixture(fixture, result, json_valid, llm_client))
                finally:
                    ledger.close()
    finally:
        close = getattr(llm_client, "close", None)
        if callable(close):
            close()
    return results


def _build_eval_client(mode: str, provider: str | None) -> RecordingFallbackLLMClient:
    if mode == "ci":
        return RecordingFallbackLLMClient(primary=RecordedLLMClient(EVAL_FIXTURES))

    config = LLMProviderConfig.from_env("LORE_LLM")
    if provider:
        config = LLMProviderConfig(
            name=config.name,
            model=provider,
            base_url=config.base_url,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
        )
    return RecordingFallbackLLMClient(primary=LLMClient(config))


def _fake_response(fixture: dict[str, Any]) -> dict[str, Any]:
    capture = fixture["capture"]
    name = fixture["name"]
    target = capture.get("suggested_target_page") or f"inbox/{name}"
    base_claim = {
        "subject": target,
        "predicate": "states",
        "object": capture["observation"],
        "confidence": capture.get("confidence", "medium"),
        "actor": capture.get("actor"),
        "lane": capture.get("lane"),
        "observed_at": capture.get("observed_at"),
        "valid_from": capture.get("valid_from"),
        "source_page_ids": [f"inbox/eval/{name}"],
        "evidence": capture["observation"],
    }
    if name == "ambiguous_noisy_capture":
        return {"entities": [], "claims": [], "edges": [], "invalidations": []}

    entities = [
        {
            "subject": target,
            "name": _entity_name(target, capture["title"]),
            "entity_type": _entity_type(target),
        }
    ]
    for label, page_id in re.findall(r"\[\[([^|\]]+)\|([^\]]+)\]\]", capture["observation"]):
        entities.append({"subject": page_id, "name": label, "entity_type": _entity_type(page_id)})

    claims = [base_claim]
    if name == "multi_entity_capture":
        claims.append(
            {
                **base_claim,
                "object": "Lore integrates with services/workflow-engine and projects/example-project for task management.",
            }
        )
    invalidations = []
    if name == "contradiction_capture":
        invalidations.append(
            {
                "target_claim": "Lore previously used PostgreSQL",
                "reason": "Capture states Lore now uses SQLite exclusively and supersedes earlier documentation.",
                "source_page_ids": [f"inbox/eval/{name}"],
            }
        )
    return {"entities": entities, "claims": claims, "edges": [], "invalidations": invalidations}


def _write_capture(repo: LoreRepository, fixture: dict[str, Any]) -> str:
    capture = fixture["capture"]
    page_id = f"inbox/eval/{fixture['name']}"
    frontmatter_keys = [
        "title",
        "actor",
        "lane",
        "observed_at",
        "suggested_target_page",
        "confidence",
        "valid_from",
    ]
    lines = ["---", "kind: capture", "visibility: internal", "status: draft"]
    for key in frontmatter_keys:
        value = capture.get(key)
        if value is not None:
            lines.append(f"{key}: {value}")
    lines.extend(
        [
            "---",
            "",
            f"# {capture['title']}",
            "",
            f"<!-- eval-fixture: {fixture['name']} -->",
            "",
            capture["observation"],
        ]
    )
    repo.upsert_page(page_id, "\n".join(lines))
    return page_id


def _metrics_for_fixture(
    fixture: dict[str, Any],
    extraction_result: Any,
    json_valid: bool,
    llm_client: Any,
) -> dict[str, Any]:
    expected = fixture["expected"]
    claims = extraction_result.claims
    entities = extraction_result.entities
    schema_valid = _schema_valid(claims)
    provenance_completeness = _provenance_completeness(claims, expected.get("provenance_fields", []))
    hallucination_count = _hallucination_count(claims, entities, expected.get("must_not_contain_entities", []))
    missed_critical_claims = _missed_critical_claims(fixture, claims, entities)
    return {
        "name": fixture["name"],
        "json_valid": json_valid,
        "schema_valid": schema_valid,
        "provenance_completeness": provenance_completeness,
        "hallucination_count": hallucination_count,
        "missed_critical_claims": missed_critical_claims,
        "fallback_used": bool(getattr(llm_client, "last_fallback_used", False)),
        "claims": len(claims),
        "entities": len(entities),
    }


def _schema_valid(claims: list[ExtractedClaim]) -> bool:
    for claim in claims:
        try:
            ExtractedClaim.model_validate(claim.model_dump())
        except Exception:
            return False
    return True


def _provenance_completeness(claims: list[ExtractedClaim], fields: list[str]) -> float:
    if not fields:
        return 1.0
    if not claims:
        return 1.0
    present = 0
    for field in fields:
        if any(bool(getattr(claim, field, None)) for claim in claims):
            present += 1
    return present / len(fields)


def _hallucination_count(
    claims: list[ExtractedClaim],
    entities: list[Any],
    forbidden_entities: list[str],
) -> int:
    if not forbidden_entities:
        return 0
    haystack = " ".join(
        [
            *(claim.subject for claim in claims),
            *(claim.object for claim in claims),
            *(entity.target_page_hint or entity.name for entity in entities),
        ]
    ).casefold()
    return sum(1 for entity in forbidden_entities if entity.casefold() in haystack)


def _missed_critical_claims(
    fixture: dict[str, Any],
    claims: list[ExtractedClaim],
    entities: list[Any],
) -> int:
    expected = fixture["expected"]
    missed = 0
    if len(claims) < expected.get("min_claims", 0):
        missed += expected["min_claims"] - len(claims)
    if len(entities) < expected.get("min_entities", 0):
        missed += expected["min_entities"] - len(entities)
    must_have_subject = expected.get("must_have_subject")
    if must_have_subject and not any(claim.subject == must_have_subject for claim in claims):
        missed += 1
    return missed


def _fixture_name_from_prompt(user_prompt: str) -> str:
    match = re.search(r"<!--\s*eval-fixture:\s*([A-Za-z0-9_-]+)\s*-->", user_prompt)
    if not match:
        raise KeyError("eval fixture marker not found in prompt")
    return match.group(1)


def _entity_name(page_id: str, fallback: str) -> str:
    if "/" not in page_id:
        return fallback
    return page_id.rsplit("/", 1)[1].replace("-", " ").title()


def _entity_type(page_id: str) -> str:
    if page_id.startswith("services/"):
        return "service"
    if page_id.startswith("projects/"):
        return "project"
    return "concept"


def _print_summary(results: list[dict[str, Any]]) -> None:
    headers = [
        "fixture",
        "json",
        "schema",
        "prov",
        "halluc",
        "missed",
        "fallback",
    ]
    print(" | ".join(headers))
    print(" | ".join("-" * len(header) for header in headers))
    for result in results:
        print(
            " | ".join(
                [
                    result["name"],
                    str(result["json_valid"]),
                    str(result["schema_valid"]),
                    f"{result['provenance_completeness']:.2f}",
                    str(result["hallucination_count"]),
                    str(result["missed_critical_claims"]),
                    str(result["fallback_used"]),
                ]
            )
        )


def _passes(results: list[dict[str, Any]]) -> bool:
    return all(
        result["json_valid"]
        and result["schema_valid"]
        and result["provenance_completeness"] >= 0.5
        and result["hallucination_count"] == 0
        and result["missed_critical_claims"] == 0
        for result in results
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Lore extraction evals.")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--ci", action="store_true", help="Use recorded responses; no API calls.")
    mode_group.add_argument("--live", action="store_true", help="Use configured live LLM provider.")
    parser.add_argument("--provider", choices=sorted(SUPPORTED_PROVIDERS), help="Provider/model to evaluate.")
    args = parser.parse_args(argv)

    mode = "live" if args.live else "ci"
    if mode == "ci" and args.provider:
        print("--provider is ignored in --ci mode", file=sys.stderr)
    results = run_eval(mode=mode, provider=args.provider)
    _print_summary(results)
    return 0 if _passes(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
