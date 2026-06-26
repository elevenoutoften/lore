from __future__ import annotations

import re
from typing import Any

from .schemas import MemoryContextCitation, MemoryRecallClaim, RagExpandedResult

_TOKEN_RE = re.compile(r"\S+")


def estimate_context_tokens(text: str) -> int:
    """Return Lore's deterministic whitespace-token estimate for context budgets."""
    return len(_TOKEN_RE.findall(text))


def build_memory_context(
    *,
    query: str | None,
    claims: list[MemoryRecallClaim],
    rag_results: list[RagExpandedResult],
    max_tokens: int,
    max_chars: int,
) -> dict[str, Any]:
    """Format recall and RAG rows into a bounded markdown context block."""
    max_tokens = max(1, int(max_tokens))
    max_chars = max(1, int(max_chars))
    lines: list[str] = ["## Lore Memory Context"]
    query_text = _compact_inline(query or "general recall", limit=160)
    lines.append(f"Query: {query_text}")

    citations: list[MemoryContextCitation] = []
    if claims:
        lines.extend(["", "### Claims"])
        for claim in claims:
            line, citation = _claim_context_line(claim)
            lines.append(line)
            citations.append(citation)

    if rag_results:
        lines.extend(["", "### Pages"])
        for result in rag_results:
            line, result_citations = _rag_context_line(result)
            lines.append(line)
            citations.extend(result_citations)

    if not claims and not rag_results:
        lines.extend(["", "No recalled claims or page context matched the request."])

    context, truncated = _bound_lines(lines, max_tokens=max_tokens, max_chars=max_chars)
    visible_citations = [citation for citation in citations if f"[{citation.ref}]" in context]
    return {
        "context": context,
        "citations": _dedupe_citations(visible_citations),
        "token_count": estimate_context_tokens(context),
        "char_count": len(context),
        "max_tokens": max_tokens,
        "max_chars": max_chars,
        "truncated": truncated,
    }


def _claim_context_line(claim: MemoryRecallClaim) -> tuple[str, MemoryContextCitation]:
    ref = f"claim:{claim.candidate_id}"
    triple = " ".join(part for part in [claim.subject, claim.predicate, claim.object] if part).strip()
    if not triple:
        triple = claim.candidate_id
    details = [
        f"score {claim.recall_score:.3f}",
        f"strength {claim.strength:.3f}",
    ]
    if claim.confidence:
        details.append(f"confidence {claim.confidence}")
    if claim.source_page_ids:
        details.append("pages " + ", ".join(claim.source_page_ids[:3]))
    if claim.source_capture_ids:
        details.append("captures " + ", ".join(claim.source_capture_ids[:3]))
    line = f"- [{ref}] {_compact_inline(triple, limit=260)} ({'; '.join(details)})"
    citation = MemoryContextCitation(
        ref=ref,
        kind="claim",
        id=claim.candidate_id,
        source_page_ids=claim.source_page_ids,
        source_capture_ids=claim.source_capture_ids,
    )
    return line, citation


def _rag_context_line(result: RagExpandedResult) -> tuple[str, list[MemoryContextCitation]]:
    ref = f"page:{result.page_id}"
    title = result.title or result.page_id
    snippets = [text for text in result.citations if text]
    snippet = snippets[0] if snippets else result.relevant_because
    details = [f"score {result.score:.3f}"]
    if result.sources:
        details.append("sources " + ", ".join(result.sources[:4]))
    if result.supporting_claims:
        details.append("claims " + ", ".join(result.supporting_claims[:3]))
    if result.source_refs:
        details.append("refs " + ", ".join(result.source_refs[:3]))
    summary = _compact_inline(snippet or "retrieved page context", limit=260)
    line = f"- [{ref}] {_compact_inline(title, limit=120)}: {summary} ({'; '.join(details)})"
    citations = [
        MemoryContextCitation(
            ref=ref,
            kind="page",
            id=result.page_id,
            source_page_ids=[result.page_id],
            source_capture_ids=[],
        )
    ]
    for claim_id in result.supporting_claims[:3]:
        claim_ref = f"claim:{claim_id}"
        citations.append(
            MemoryContextCitation(
                ref=claim_ref,
                kind="claim",
                id=claim_id,
                source_page_ids=[result.page_id],
                source_capture_ids=[],
            )
        )
    return line, citations


def _bound_lines(lines: list[str], *, max_tokens: int, max_chars: int) -> tuple[str, bool]:
    kept: list[str] = []
    truncated = False
    for line in lines:
        candidate_lines = [*kept, line]
        candidate = _render_lines(candidate_lines)
        if len(candidate) <= max_chars and estimate_context_tokens(candidate) <= max_tokens:
            kept.append(line)
        else:
            truncated = True
            break
    context = _render_lines(kept)
    if truncated:
        footer = "_Context truncated by requested budget._"
        candidate = _render_lines([*kept, "", footer])
        if len(candidate) <= max_chars and estimate_context_tokens(candidate) <= max_tokens:
            context = candidate
    return context, truncated


def _render_lines(lines: list[str]) -> str:
    return "\n".join(lines).strip() + "\n"


def _compact_inline(value: str, *, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _dedupe_citations(citations: list[MemoryContextCitation]) -> list[MemoryContextCitation]:
    seen: set[str] = set()
    deduped: list[MemoryContextCitation] = []
    for citation in citations:
        if citation.ref in seen:
            continue
        seen.add(citation.ref)
        deduped.append(citation)
    return deduped
