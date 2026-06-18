from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from .capture import CAPTURE_INTAKE_SUMMARY
from .config import LoreConfig
from .ledger import LedgerDB
from .llm_provider import FallbackLLMClient, LLMError, LLMUnavailableError, NoLlmClient
from .repository import LoreRepository, optional_string, string_list
from .schemas import (
    ExtractedClaim,
    ExtractedEdge,
    ExtractedEntity,
    ExtractedInvalidation,
    ExtractionResult,
    ExtractionRetryResponse,
    PageDetail,
    PageSummary,
)

WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
PAGE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CONTRADICTION_PATTERN = re.compile(r"(?im)^\s*(?:contradicts|supersedes|replaces|invalidates)\s*:\s*(.+)$")


def get_unprocessed_captures(
    repo: LoreRepository,
    limit: int = 50,
    *,
    ledger_db: LedgerDB | None = None,
) -> list[PageSummary]:
    """Get captures with status=draft that have no extraction record in the ledger."""

    ledger = _ledger(ledger_db)
    captures = [
        page
        for page in repo.list_pages(kind="capture")
        if page.status == "draft"
        and page.id.startswith(("inbox/", "notes/"))
        and not ledger.is_capture_extracted(page.id)
    ]
    captures.sort(key=lambda page: page.id, reverse=True)
    return captures[: max(1, min(limit, 200))]


def extract_from_captures(
    repo: LoreRepository,
    capture_ids: list[str] | None = None,
    batch_size: int = 10,
    dry_run: bool = True,
    *,
    ledger_db: LedgerDB | None = None,
    llm_client: Any | None = None,
) -> ExtractionResult:
    """
    Run extraction on captures.

    If an LLM client is supplied, each capture is attempted with the LLM-backed
    extractor first. Failed or invalid LLM output falls back to the deterministic
    extractor for that capture.
    """

    ledger = _ledger(ledger_db)
    selected = _select_captures(repo, ledger, capture_ids, batch_size)
    processed_at = datetime.now(UTC).isoformat()
    batch_id = str(uuid.uuid4())

    entities: list[ExtractedEntity] = []
    claims: list[ExtractedClaim] = []
    edges: list[ExtractedEdge] = []
    invalidations: list[ExtractedInvalidation] = []
    source_capture_ids: list[str] = []

    seen_entities: set[tuple[str, str | None]] = set()
    seen_edges: set[tuple[str, str, str, tuple[str, ...]]] = set()
    active_llm_client = llm_client or NoLlmClient()

    for capture in selected:
        llm_result = None
        llm_failure: Exception | None = None
        from .llm_extractor import llm_extract_capture

        try:
            llm_result = llm_extract_capture(capture, active_llm_client)
        except (LLMError, LLMUnavailableError) as exc:
            llm_failure = exc
        except ValueError as exc:
            if isinstance(active_llm_client, FallbackLLMClient) and active_llm_client.escalation is not None:
                try:
                    llm_result = llm_extract_capture(capture, active_llm_client.escalation)
                except (LLMError, LLMUnavailableError, ValueError) as escalation_exc:
                    llm_failure = escalation_exc
            else:
                llm_failure = exc
        except Exception as exc:
            llm_failure = exc

        try:
            if llm_result is not None:
                capture_entities = llm_result.get("entities", [])
                capture_claims = llm_result.get("claims", [])
                capture_edges = llm_result.get("edges", [])
                capture_invalidations = llm_result.get("invalidations", [])
                llm_observed_at = datetime.now(UTC).isoformat()
                for claim in capture_claims:
                    if not claim.observed_at:
                        claim.observed_at = llm_observed_at
            else:
                capture_entities, capture_claims, capture_edges, capture_invalidations = _extract_capture(repo, capture)

            staged_entities: list[ExtractedEntity] = []
            staged_entity_keys: set[tuple[str, str | None]] = set()
            staged_claims: list[ExtractedClaim] = []
            staged_edges: list[ExtractedEdge] = []
            staged_edge_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()

            for entity in capture_entities:
                key = (entity.name.casefold(), entity.target_page_hint)
                if key not in seen_entities and key not in staged_entity_keys:
                    staged_entities.append(entity)
                    staged_entity_keys.add(key)
            staged_claims.extend(capture_claims)
            for edge in capture_edges:
                key = (
                    edge.source_entity.casefold(),
                    edge.relationship_type,
                    edge.target_entity.casefold(),
                    tuple(sorted(edge.source_page_ids)),
                )
                if key not in seen_edges and key not in staged_edge_keys:
                    staged_edges.append(edge)
                    staged_edge_keys.add(key)

            # Record LLM quality failures even when deterministic fallback succeeds.
            # This preserves invalid or low-quality provider output for review.
            if (
                llm_failure is not None
                and llm_result is None
                and llm_client is not None
                and not isinstance(active_llm_client, NoLlmClient)
            ):
                ledger.store_deadletter(
                    capture_id=capture.id,
                    provider=_deadletter_provider(active_llm_client, fallback_failed=False),
                    failure_kind=_deadletter_failure_kind(llm_failure, None),
                    failure_detail=_deadletter_failure_detail(llm_failure, None),
                    payload=_deadletter_payload(capture),
                    batch_id=batch_id,
                )

            entities.extend(staged_entities)
            seen_entities.update(staged_entity_keys)
            claims.extend(staged_claims)
            edges.extend(staged_edges)
            seen_edges.update(staged_edge_keys)
            invalidations.extend(capture_invalidations)
            source_capture_ids.append(capture.id)
        except Exception as processing_exc:
            fallback_failed = llm_failure is not None and llm_result is None
            ledger.store_deadletter(
                capture_id=capture.id,
                provider=_deadletter_provider(active_llm_client, fallback_failed=fallback_failed),
                failure_kind=_deadletter_failure_kind(llm_failure, processing_exc),
                failure_detail=_deadletter_failure_detail(llm_failure, processing_exc),
                payload=_deadletter_payload(capture),
                batch_id=batch_id,
            )
            continue

    result = ExtractionResult(
        batch_id=batch_id,
        entities=entities,
        claims=claims,
        edges=edges,
        invalidations=invalidations,
        source_capture_ids=source_capture_ids,
        processed_at=processed_at,
    )
    if not dry_run:
        ledger.store_extraction_result(result)
    return result


def compute_extraction_hash(subject: str, predicate: str, object: str, source_page_ids: list[str]) -> str:
    """Deterministic SHA-256 hash for semantic candidate deduplication.

    Source pages are deliberately excluded: corroborating the same normalized
    fact from another capture must reinforce one claim instead of creating a
    duplicate row. ``source_page_ids`` remains in the signature for callers
    that already supply it; the ledger merges those sources on reinforcement.
    """

    del source_page_ids

    normalized = {
        "subject": " ".join(subject.casefold().split()),
        "predicate": " ".join(predicate.casefold().split()),
        "object": " ".join(object.casefold().split()),
    }
    payload = "\n".join([normalized["subject"], normalized["predicate"], normalized["object"]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_capture_extracted(ledger_db, capture_id: str) -> bool:
    """Check if a capture has already been processed by the extraction pipeline."""

    return bool(ledger_db.is_capture_extracted(capture_id))


def retry_deadletter(
    repo: LoreRepository,
    deadletter_id: str,
    *,
    ledger_db: LedgerDB | None = None,
    llm_client: Any | None = None,
) -> ExtractionRetryResponse | None:
    """Reset and retry one extraction dead-letter by id."""

    ledger = _ledger(ledger_db)
    deadletter = ledger.get_deadletter(deadletter_id)
    if deadletter is None:
        return None

    capture_id = str(deadletter.get("capture_id") or "")
    candidates_before = len(ledger.get_candidates(capture_id=capture_id, limit=500)) if capture_id else 0
    if str(deadletter.get("status") or "") == "resolved":
        return ExtractionRetryResponse(
            deadletter_id=deadletter_id,
            capture_id=capture_id or None,
            retried=False,
            resolved=True,
            candidates=candidates_before,
        )

    active_llm_client = llm_client or NoLlmClient()
    resolved_by = "deterministic" if isinstance(active_llm_client, NoLlmClient) else "llm"
    ledger.increment_retry(deadletter_id)
    if capture_id:
        ledger.reset_extraction(capture_ids=[capture_id], delete_candidates=True)

    try:
        result = extract_from_captures(
            repo,
            capture_ids=[capture_id],
            batch_size=1,
            dry_run=False,
            ledger_db=ledger,
            llm_client=active_llm_client,
        )
    except Exception as exc:
        return ExtractionRetryResponse(
            deadletter_id=deadletter_id,
            capture_id=capture_id or None,
            retried=True,
            resolved=False,
            candidates=0,
            error=str(exc),
        )

    candidates = len(ledger.get_candidates(capture_id=capture_id, limit=500)) if capture_id else 0
    resolved = bool(capture_id and capture_id in result.source_capture_ids)
    if resolved:
        resolved = ledger.resolve_deadletter(deadletter_id, resolved_by=resolved_by)
    return ExtractionRetryResponse(
        deadletter_id=deadletter_id,
        capture_id=capture_id or None,
        retried=True,
        resolved=resolved,
        candidates=candidates,
        source_capture_ids=result.source_capture_ids,
    )


def _select_captures(
    repo: LoreRepository,
    ledger: LedgerDB,
    capture_ids: list[str] | None,
    batch_size: int,
) -> list[PageDetail]:
    limit = max(1, min(batch_size, 50))
    if capture_ids is None:
        summaries = get_unprocessed_captures(repo, limit=limit, ledger_db=ledger)
        details = [repo.read_page(summary.id) for summary in summaries]
        return [detail for detail in details if detail is not None]

    details: list[PageDetail] = []
    for capture_id in capture_ids[:limit]:
        if ledger.is_capture_extracted(capture_id):
            continue
        detail = repo.read_page(capture_id)
        if detail is None:
            continue
        if detail.frontmatter.get("kind") != "capture" or detail.status != "draft":
            continue
        details.append(detail)
    return details


def _extract_capture(
    repo: LoreRepository,
    capture: PageDetail,
) -> tuple[list[ExtractedEntity], list[ExtractedClaim], list[ExtractedEdge], list[ExtractedInvalidation]]:
    frontmatter = capture.frontmatter
    confidence = optional_string(frontmatter.get("confidence")) or "unknown"
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    actor = optional_string(frontmatter.get("actor"))
    lane = optional_string(frontmatter.get("lane"))
    observed_at = optional_string(frontmatter.get("observed_at"))
    valid_from = optional_string(frontmatter.get("valid_from"))
    valid_until = optional_string(frontmatter.get("valid_until"))
    source_task = optional_string(frontmatter.get("source_task"))
    decision_id = optional_string(frontmatter.get("decision_id"))
    trace_id = optional_string(frontmatter.get("trace_id"))
    policies_applied = string_list(frontmatter.get("policies_applied"))
    epistemic_status = frontmatter.get("epistemic_status")
    suggested_target = optional_string(frontmatter.get("suggested_target_page"))
    front_summary = optional_string(frontmatter.get("summary"))
    if front_summary and front_summary.casefold().strip() == CAPTURE_INTAKE_SUMMARY.casefold():
        front_summary = None
    summary = front_summary or _first_meaningful_line(capture.body)
    evidence = optional_string(frontmatter.get("evidence")) or summary
    source_pages = [capture.id]

    entities: list[ExtractedEntity] = []
    linked_entities = _wikilink_entities(capture.body)
    for link in linked_entities:
        entities.append(
            ExtractedEntity(
                name=link["label"],
                entity_type=_entity_type_for_page_id(link["page_id"]),
                target_page_hint=link["page_id"],
            )
        )
    if suggested_target:
        entities.append(
            ExtractedEntity(
                name=_entity_name(suggested_target, capture.title),
                entity_type=_entity_type_for_page_id(suggested_target),
                summary=summary,
                target_page_hint=suggested_target,
            )
        )

    subject = _claim_subject(suggested_target, capture.title)
    claim_object = summary or capture.title
    claims = [
        ExtractedClaim(
            subject=subject,
            predicate="states",
            object=claim_object,
            confidence=confidence,
            actor=actor,
            lane=lane,
            observed_at=observed_at,
            valid_from=valid_from,
            valid_until=valid_until,
            source_task=source_task,
            decision_id=decision_id,
            trace_id=trace_id,
            policies_applied=policies_applied,
            evidence=evidence,
            section=None,
            source_page_ids=source_pages,
            epistemic_status=epistemic_status,
        )
    ]

    edges: list[ExtractedEdge] = []
    for link in linked_entities:
        target = link["page_id"]
        if target == suggested_target:
            continue
        edges.append(
            ExtractedEdge(
                source_entity=suggested_target or capture.id,
                relationship_type="mentions",
                target_entity=target,
                strength=0.5,
                evidence=link["raw"],
                source_page_ids=[capture.id],
            )
        )

    invalidations = _extract_invalidations(repo, capture, suggested_target, claim_object)
    return entities, claims, edges, invalidations


def _deadletter_provider(llm_client: Any, *, fallback_failed: bool) -> str:
    if fallback_failed:
        return "fallback"
    model = getattr(getattr(llm_client, "primary", None), "config", None)
    provider = getattr(model, "model", None)
    if provider:
        return str(provider)
    return llm_client.__class__.__name__


def _deadletter_failure_kind(llm_failure: Exception | None, fallback_exc: Exception | None) -> str:
    if fallback_exc is not None:
        if llm_failure is None:
            return "processing_error"
        return "fallback_exhausted"
    if llm_failure is None:
        return "processing_error"
    if isinstance(llm_failure, ValueError):
        return "schema_invalid"
    message = str(llm_failure).casefold()
    if "timeout" in message:
        return "timeout"
    return "llm_error"


def _deadletter_failure_detail(llm_failure: Exception | None, fallback_exc: Exception | None) -> str:
    if fallback_exc is None:
        return str(llm_failure or "")
    if llm_failure is None:
        return str(fallback_exc)
    return f"llm={llm_failure}; fallback={fallback_exc}"


def _deadletter_payload(capture: PageDetail) -> str:
    return json.dumps(
        {
            "capture_id": capture.id,
            "title": capture.title,
            "frontmatter": capture.frontmatter,
            "body": capture.body,
        },
        ensure_ascii=False,
    )


def _is_plausible_page_id(text: str) -> bool:
    text = text.strip().strip("/")
    if not text or text.casefold() in {"page-id", "page_id"}:
        return False
    if "/" not in text and text != text.casefold():
        return False
    parts = [part for part in text.split("/") if part]
    if not parts:
        return False
    return all(PAGE_SEGMENT_RE.fullmatch(part) for part in parts) and not any(part in {".", ".."} for part in parts)


def _wikilink_entities(body: str) -> list[dict[str, str]]:
    entities: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in WIKILINK_PATTERN.finditer(body):
        raw = match.group(1).strip()
        if not raw:
            continue
        if "|" in raw:
            left, right = [part.strip() for part in raw.split("|", 1)]
            if _is_plausible_page_id(right):
                page_id, label = right.strip().strip("/"), left
            elif _is_plausible_page_id(left):
                page_id, label = left.strip().strip("/"), right
            else:
                continue
        else:
            page_id = raw
            label = raw.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
        page_id = page_id.strip().strip("/")
        if not page_id or page_id in seen:
            continue
        seen.add(page_id)
        entities.append({"page_id": page_id, "label": label or page_id, "raw": match.group(0)})
    return entities


def _extract_invalidations(
    repo: LoreRepository,
    capture: PageDetail,
    suggested_target: str | None,
    new_fact: str,
) -> list[ExtractedInvalidation]:
    invalidations: list[ExtractedInvalidation] = []
    for match in CONTRADICTION_PATTERN.finditer(capture.body):
        old_fact = match.group(1).strip()
        if old_fact:
            invalidations.append(
                ExtractedInvalidation(
                    old_fact=old_fact,
                    new_fact=new_fact,
                    reason="Capture explicitly marks this fact as superseded or contradicted.",
                    target_page_ids=[suggested_target] if suggested_target else [],
                )
            )
    if suggested_target and not invalidations:
        target = repo.read_page(suggested_target)
        if target and _has_conflicting_marker(capture.body, target.body):
            invalidations.append(
                ExtractedInvalidation(
                    old_fact=target.title,
                    new_fact=new_fact,
                    reason="Capture contains contradiction language for an existing canonical page.",
                    target_page_ids=[suggested_target],
                )
            )
    return invalidations


def _first_meaningful_line(body: str) -> str | None:
    for line in body.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#") or _is_intake_warning(cleaned):
            continue
        return cleaned[:500]
    return None


def _is_intake_warning(line: str) -> bool:
    stripped = line.strip()
    folded = stripped.casefold()
    return (
        stripped.startswith(">")
        and "rough" in folded
        and ("intake" in folded or "not canonical" in folded or "not accepted" in folded)
    )


def _entity_type_for_page_id(page_id: str | None) -> str:
    if not page_id:
        return "concept"
    first = page_id.split("/", 1)[0]
    return {
        "people": "person",
        "projects": "project",
        "services": "service",
        "tools": "tool",
        "concepts": "concept",
        "procedures": "procedure",
        "runbooks": "runbook",
    }.get(first, "concept")


def _claim_subject(suggested_target: str | None, title: str) -> str:
    return suggested_target or title


def _entity_name(page_id: str, fallback: str) -> str:
    return page_id.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title() or fallback


def _has_conflicting_marker(capture_body: str, target_body: str) -> bool:
    capture_lower = capture_body.casefold()
    if not any(word in capture_lower for word in ("contradicts", "supersedes", "invalidates", "no longer")):
        return False
    target_title = _first_meaningful_line(target_body)
    return target_title is not None


def _ledger(ledger_db: LedgerDB | None) -> LedgerDB:
    if ledger_db is not None:
        if ledger_db._connection is None:
            ledger_db.initialize()
        return ledger_db
    ledger = LedgerDB(LoreConfig().ledger_db)
    ledger.initialize()
    return ledger
