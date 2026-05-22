from __future__ import annotations

from typing import Any, Iterable

from .repository import LoreRepository, optional_string, string_list
from .schemas import CaptureRequest, ContextRef, ProvenanceRef, TraceEntry


def merge_capture_provenance(payload: CaptureRequest, *, related_pages: list[str] | None = None) -> ProvenanceRef:
    provenance = _copy_provenance(payload.provenance)
    _extend_unique(provenance.page_ids, related_pages if related_pages is not None else payload.related_pages)
    _extend_unique(provenance.task_ids, [payload.task_id, payload.decision_id])
    _extend_unique(provenance.trace_ids, [payload.trace_id])
    _extend_unique(provenance.policy_ids, payload.policies_applied)
    _extend_unique(provenance.source_paths, payload.source_paths)
    _extend_unique(provenance.source_urls, payload.source_urls)
    if payload.source_task and provenance.source_task is None:
        provenance.source_task = payload.source_task
    if payload.actor and provenance.actor is None:
        provenance.actor = payload.actor
    if payload.tool_calls:
        provenance.tool_calls.extend(payload.tool_calls)
    _extend_unique(provenance.constraints, payload.constraints)
    return provenance


def merge_trace_provenance(trace: TraceEntry) -> ProvenanceRef:
    provenance = _copy_provenance(trace.provenance)
    if trace.actor and provenance.actor is None:
        provenance.actor = trace.actor
    _extend_unique(provenance.constraints, trace.constraints)
    _extend_unique(provenance.policy_ids, trace.policy_refs)
    for tool_ref in trace.tool_refs:
        provenance.tool_calls.append(tool_ref.model_dump(mode="json"))
    for ref in trace.context_refs:
        _merge_context_ref(provenance, ref)
    _merge_related_ids(provenance, trace.related_ids)
    return provenance


def provenance_from_frontmatter(frontmatter: dict[str, Any]) -> ProvenanceRef:
    raw = frontmatter.get("provenance")
    if isinstance(raw, dict):
        provenance = ProvenanceRef.model_validate(raw)
    else:
        provenance = ProvenanceRef()
    _extend_unique(provenance.page_ids, string_list(frontmatter.get("related")))
    _extend_unique(provenance.trace_ids, [frontmatter.get("trace_id")])
    _extend_unique(provenance.task_ids, [frontmatter.get("task_id"), frontmatter.get("decision_id")])
    _extend_unique(provenance.policy_ids, string_list(frontmatter.get("policies_applied")))
    _extend_unique(provenance.source_paths, string_list(frontmatter.get("source_paths")))
    _extend_unique(provenance.source_urls, string_list(frontmatter.get("source_urls")))
    source_task = optional_string(frontmatter.get("source_task"))
    if source_task and provenance.source_task is None:
        provenance.source_task = source_task
    actor = optional_string(frontmatter.get("actor"))
    if actor and provenance.actor is None:
        provenance.actor = actor
    tool_calls = frontmatter.get("tool_calls")
    if isinstance(tool_calls, list):
        provenance.tool_calls.extend([item for item in tool_calls if isinstance(item, dict)])
    _extend_unique(provenance.constraints, string_list(frontmatter.get("constraints")))
    return provenance


def get_capture_provenance(repo: LoreRepository, capture_id: str) -> ProvenanceRef | None:
    page = repo.read_page(capture_id)
    if page is None or page.frontmatter.get("kind") != "capture":
        return None
    return provenance_from_frontmatter(page.frontmatter)


def get_page_provenance(repo: LoreRepository, page_id: str) -> ProvenanceRef:
    provenance = ProvenanceRef(page_ids=[page_id])
    for page in repo.list_pages(kind="capture"):
        detail = repo.read_page(page.id)
        if detail is None:
            continue
        capture_provenance = provenance_from_frontmatter(detail.frontmatter)
        if page_id in capture_provenance.page_ids:
            _extend_unique(provenance.capture_ids, [page.id])
            _extend_unique(provenance.trace_ids, capture_provenance.trace_ids)
            _extend_unique(provenance.policy_ids, capture_provenance.policy_ids)
            _extend_unique(provenance.candidate_ids, capture_provenance.candidate_ids)
            _extend_unique(provenance.task_ids, capture_provenance.task_ids)
            _extend_unique(provenance.source_paths, capture_provenance.source_paths)
            _extend_unique(provenance.source_urls, capture_provenance.source_urls)
            if provenance.source_task is None:
                provenance.source_task = capture_provenance.source_task
            if provenance.actor is None:
                provenance.actor = capture_provenance.actor
            provenance.tool_calls.extend(capture_provenance.tool_calls)
            _extend_unique(provenance.constraints, capture_provenance.constraints)
    return provenance


def _copy_provenance(provenance: ProvenanceRef | None) -> ProvenanceRef:
    return ProvenanceRef.model_validate(provenance.model_dump(mode="json") if provenance is not None else {})


def _extend_unique(target: list[str], values: Iterable[Any]) -> None:
    seen = set(target)
    for value in values:
        cleaned = optional_string(value)
        if cleaned and cleaned not in seen:
            target.append(cleaned)
            seen.add(cleaned)


def _merge_context_ref(provenance: ProvenanceRef, ref: ContextRef) -> None:
    if ref.type == "page":
        _extend_unique(provenance.page_ids, [ref.id])
    elif ref.type == "capture":
        _extend_unique(provenance.capture_ids, [ref.id])
    elif ref.type == "task":
        _extend_unique(provenance.task_ids, [ref.id])
    elif ref.type == "candidate":
        _extend_unique(provenance.candidate_ids, [ref.id])


def _merge_related_ids(provenance: ProvenanceRef, related_ids: dict[str, str]) -> None:
    mapping = {
        "page_id": provenance.page_ids,
        "capture_id": provenance.capture_ids,
        "trace_id": provenance.trace_ids,
        "policy_id": provenance.policy_ids,
        "policy_ref": provenance.policy_ids,
        "candidate_id": provenance.candidate_ids,
        "task_id": provenance.task_ids,
        "decision_id": provenance.task_ids,
    }
    for key, value in related_ids.items():
        target = mapping.get(key)
        if target is not None:
            _extend_unique(target, [value])
