"""LLM-backed extraction with deterministic fallback.

This module normalizes provider JSON into the same extraction schemas used by
the deterministic extractor. Callers decide whether to fall back when this
module raises.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from .llm_provider import FallbackLLMClient, LLMError, LLMJsonError
from .repository import LoreRepository, string_list
from .schemas import ExtractedClaim, ExtractedEdge, ExtractedEntity, ExtractedInvalidation

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are a knowledge extraction engine. Given a markdown document, extract:
1. Entities: named things referenced in the document (services, concepts, people, tools)
2. Claims: subject-predicate-object assertions with confidence level (high/medium/low)
3. Edges: relationships between entities (mentions, depends_on, contradicts, supports, supersedes)
4. Invalidations: statements that supersede or contradict existing claims

Output a JSON object with keys "entities", "claims", "edges", "invalidations".
Each entity has: subject (page ID like "services/api"), name (human-readable label), entity_type (service/concept/person/tool/system).
Each claim has: subject (page ID), predicate, object, confidence (high/medium/low), source_page_ids (list), observed_at, valid_from, valid_until, actor.
Each edge has: source (page ID), target (page ID), edge_type (mentions/depends_on/contradicts/supports/supersedes).
Each invalidation has: target_claim (page ID or claim text), reason, source_page_ids.
Only extract what is explicitly stated. Do not infer unstated relationships."""


def llm_extract_capture(
    page_detail: Any,
    llm_client: FallbackLLMClient,
    repo: LoreRepository | None = None,
) -> dict[str, list] | None:
    """Extract entities, claims, edges, and invalidations from a capture using an LLM."""

    del repo
    page_id = getattr(page_detail, "id", "") or ""
    title = getattr(page_detail, "title", "") or ""
    body = getattr(page_detail, "body", "") or ""
    frontmatter = getattr(page_detail, "frontmatter", {}) or {}
    kind = frontmatter.get("kind", "page")
    tags = string_list(frontmatter.get("tags"))
    suggested_target = frontmatter.get("suggested_target_page") or ""

    user_prompt = (
        f"Page ID: {page_id}\n"
        f"Title: {title}\n"
        f"Kind: {kind}\n"
        f"Suggested target page: {suggested_target}\n"
        f"Tags: {', '.join(tags)}\n\n"
        f"{body}"
    )

    try:
        result = llm_client.extract_json(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return _validate_llm_output(result, page_id, title)
    except (LLMError, LLMJsonError) as exc:
        logger.warning("LLM extraction failed for %s: %s", page_id, exc)
        raise
    except ValueError as exc:
        logger.warning("LLM output validation failed for %s: %s", page_id, exc)
        raise


def _validate_llm_output(raw: dict[str, Any], page_id: str, title: str = "") -> dict[str, list]:
    """Validate and normalize LLM JSON into extraction schema instances."""

    if not isinstance(raw, dict):
        raise ValueError(f"expected object, got {type(raw).__name__}")

    known_keys = {"entities", "claims", "edges", "invalidations"}
    if not known_keys.intersection(raw):
        raise ValueError("missing extraction result keys")

    for key in known_keys:
        value = raw.get(key, [])
        if value is None:
            raw[key] = []
        elif not isinstance(value, list):
            raise ValueError(f"{key} must be a list")

    entities: list[ExtractedEntity] = []
    claims: list[ExtractedClaim] = []
    edges: list[ExtractedEdge] = []
    invalidations: list[ExtractedInvalidation] = []

    for item in raw.get("entities", []):
        if not isinstance(item, dict):
            continue
        subject = _clean_string(item.get("subject")) or _clean_string(item.get("target_page_hint"))
        name = _clean_string(item.get("name")) or _entity_name(subject) or title or page_id
        try:
            entities.append(
                ExtractedEntity(
                    name=name,
                    entity_type=_clean_string(item.get("entity_type")) or "concept",
                    aliases=string_list(item.get("aliases")),
                    summary=_clean_string(item.get("summary")),
                    target_page_hint=subject,
                )
            )
        except ValidationError as exc:
            logger.debug("Skipping invalid LLM entity for %s: %s", page_id, exc)

    for item in raw.get("claims", []):
        if not isinstance(item, dict):
            continue
        predicate = _clean_string(item.get("predicate"))
        obj = _clean_string(item.get("object"))
        if not predicate or not obj:
            continue
        try:
            claims.append(
                ExtractedClaim(
                    subject=_clean_string(item.get("subject")) or page_id,
                    predicate=predicate,
                    object=obj,
                    confidence=_normalize_confidence(item.get("confidence")),
                    actor=_clean_string(item.get("actor")),
                    lane=_clean_string(item.get("lane")),
                    observed_at=_clean_string(item.get("observed_at")),
                    valid_from=_clean_string(item.get("valid_from")),
                    valid_until=_clean_string(item.get("valid_until")),
                    source_task=_clean_string(item.get("source_task")),
                    decision_id=_clean_string(item.get("decision_id")),
                    trace_id=_clean_string(item.get("trace_id")),
                    policies_applied=string_list(item.get("policies_applied")),
                    evidence=_clean_string(item.get("evidence")),
                    source_page_ids=_source_pages(item.get("source_page_ids"), page_id),
                    epistemic_status=item.get("epistemic_status"),
                )
            )
        except ValidationError as exc:
            logger.debug("Skipping invalid LLM claim for %s: %s", page_id, exc)

    for item in raw.get("edges", []):
        if not isinstance(item, dict):
            continue
        source = _clean_string(item.get("source")) or _clean_string(item.get("source_entity")) or page_id
        target = _clean_string(item.get("target")) or _clean_string(item.get("target_entity"))
        if not target:
            continue
        try:
            edges.append(
                ExtractedEdge(
                    source_entity=source,
                    relationship_type=(
                        _clean_string(item.get("edge_type"))
                        or _clean_string(item.get("relationship_type"))
                        or "mentions"
                    ),
                    target_entity=target,
                    strength=_normalize_strength(item.get("strength")),
                    evidence=_clean_string(item.get("evidence")),
                    source_page_ids=_source_pages(item.get("source_page_ids"), page_id),
                )
            )
        except ValidationError as exc:
            logger.debug("Skipping invalid LLM edge for %s: %s", page_id, exc)

    for item in raw.get("invalidations", []):
        if not isinstance(item, dict):
            continue
        old_fact = _clean_string(item.get("target_claim")) or _clean_string(item.get("old_fact"))
        reason = _clean_string(item.get("reason"))
        if not old_fact or not reason:
            continue
        try:
            invalidations.append(
                ExtractedInvalidation(
                    old_fact=old_fact,
                    new_fact=_clean_string(item.get("new_fact")) or title or page_id,
                    reason=reason,
                    target_page_ids=_source_pages(item.get("target_page_ids"), page_id)
                    if item.get("target_page_ids") is not None
                    else [],
                )
            )
        except ValidationError as exc:
            logger.debug("Skipping invalid LLM invalidation for %s: %s", page_id, exc)

    return {
        "entities": entities,
        "claims": claims,
        "edges": edges,
        "invalidations": invalidations,
    }


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_pages(value: Any, page_id: str) -> list[str]:
    pages = string_list(value)
    return pages or [page_id]


def _normalize_confidence(value: Any) -> str:
    confidence = (_clean_string(value) or "medium").casefold()
    if confidence not in {"high", "medium", "low"}:
        return "medium"
    return confidence


def _normalize_strength(value: Any) -> float:
    try:
        strength = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, strength))


def _entity_name(page_id: str | None) -> str | None:
    if not page_id:
        return None
    return page_id.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
