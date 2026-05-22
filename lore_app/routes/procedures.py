from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..deps import get_graph_cache, get_metrics, get_repo, get_search_index, get_templates, get_vector_store
from ..frontmatter import update_frontmatter
from ..link_graph import LinkGraphCache
from ..observability import MetricsCollector
from ..procedure_candidate import find_repeated_captures, propose_procedure_candidate
from ..rag.vector_store import VectorStore
from ..repository import InvalidPageId, LoreRepository
from ..route_utils import index_vectors_for_page, template_context
from ..schemas import (
    ProcedureArtifact,
    ProcedureCandidateProposal,
    ProcedureCandidateResponse,
    ProcedureExportRequest,
    ProcedureExportResponse,
    RepeatedCaptureGroup,
)
from ..search_index import LoreSearchIndex

router = APIRouter()


@router.get("/api/procedures/candidates", response_model=list[RepeatedCaptureGroup])
def api_find_repeated_captures(repo: LoreRepository = Depends(get_repo)):
    return find_repeated_captures(repo)


@router.post(
    "/api/procedures/candidates",
    response_model=ProcedureCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_propose_procedure_candidate(
    payload: ProcedureCandidateProposal,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    metrics: MetricsCollector = Depends(get_metrics),
):
    try:
        result = propose_procedure_candidate(
            repo,
            payload.capture_ids,
            title=payload.title,
            trigger=payload.trigger,
            lane=payload.lane,
        )
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    metrics.increment_index_size()
    search_idx.upsert_page_from_detail(result.page)
    background_tasks.add_task(index_vectors_for_page, vector_store, result.page)
    graph_cache.invalidate()
    return result


@router.get("/procedures", response_class=HTMLResponse)
def procedures_dashboard(
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
):
    groups = find_repeated_captures(repo)

    # List existing procedure-candidate pages.
    candidates = [
        page for page in repo.list_pages(kind="procedure-candidate")
    ]

    enriched_candidates = []
    for candidate in candidates:
        detail = repo.read_page(candidate.id)
        if detail is None:
            continue
        enriched_candidates.append({
            "page": candidate,
            "sources": [s for s in candidate.sources if s],
            "trigger": detail.frontmatter.get("trigger", ""),
        })

    return templates.TemplateResponse(
        request,
        "procedures.html",
        template_context(
            request,
            groups=groups,
            group_count=len(groups),
            candidates=enriched_candidates,
            candidate_count=len(enriched_candidates),
            title="Procedure Candidates",
        ),
    )


def _slugify_skill_name(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled-procedure"


@router.post("/api/procedures/export", response_model=ProcedureExportResponse)
def api_export_procedure(
    payload: ProcedureExportRequest,
    repo: LoreRepository = Depends(get_repo),
):
    """Export a procedure page as a skill artifact or enhanced markdown."""
    page = repo.read_page(payload.page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Procedure not found.")
    fm = page.frontmatter
    if fm.get("kind") not in ("procedure", "procedure-candidate"):
        raise HTTPException(status_code=422, detail="Page is not a procedure.")

    artifact = ProcedureArtifact(
        page_id=page.id,
        title=page.title or page.id,
        schema_version=fm.get("schema_version", "1.0"),
        author=fm.get("author", ""),
        trigger=fm.get("trigger", ""),
        steps=fm.get("steps", []),
        preconditions=fm.get("preconditions", []),
        postconditions=fm.get("postconditions", []),
        error_handling=fm.get("error_handling", ""),
        validated=bool(fm.get("validated", False)),
        validated_at=fm.get("validated_at"),
        source_capture_ids=fm.get("source_capture_ids", []) if isinstance(fm.get("source_capture_ids"), list) else [],
        epistemic_status=fm.get("epistemic_status"),
    )

    if payload.format == "skill":
        fm_lines = [
            "---",
            f"name: {_slugify_skill_name(artifact.title)}",
            f"description: {artifact.trigger}",
            f"version: {artifact.schema_version}",
            f"author: {artifact.author}",
            f"validated: {str(artifact.validated).lower()}",
        ]
        if artifact.validated_at:
            fm_lines.append(f"validated_at: {artifact.validated_at}")
        if artifact.epistemic_status:
            fm_lines.append(f"epistemic_status: {artifact.epistemic_status}")
        fm_lines.append("---")

        body = [f"# {artifact.title}", ""]
        if artifact.trigger:
            body.extend([f"**Trigger:** {artifact.trigger}", ""])
        if artifact.preconditions:
            body.append("## Preconditions")
            for pc in artifact.preconditions:
                body.append(f"- {pc}")
            body.append("")
        if artifact.steps:
            body.append("## Steps")
            for i, step in enumerate(artifact.steps, 1):
                body.append(f"{i}. {step}")
            body.append("")
        if artifact.postconditions:
            body.append("## Postconditions")
            for pc in artifact.postconditions:
                body.append(f"- {pc}")
            body.append("")
        if artifact.error_handling:
            body.extend(["## Error Handling", artifact.error_handling, ""])
        if artifact.source_capture_ids:
            body.append("## Source Captures")
            for cid in artifact.source_capture_ids:
                body.append(f"- {cid}")
            body.append("")

        content = "\n".join(fm_lines + body)
        filename = f"{_slugify_skill_name(artifact.title)}.md"
    else:
        content = page.content
        filename = f"{page.id.replace('/', '-')}.md"

    return ProcedureExportResponse(
        page_id=page.id,
        format=payload.format,
        content=content,
        filename=filename,
    )


@router.post("/api/procedures/{page_id:path}/validate")
def api_validate_procedure(
    page_id: str,
    repo: LoreRepository = Depends(get_repo),
):
    """Mark a procedure as validated."""
    page = repo.read_page(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Procedure not found.")
    fm = page.frontmatter
    if fm.get("kind") not in ("procedure", "procedure-candidate"):
        raise HTTPException(status_code=422, detail="Page is not a procedure.")

    fm_updates = {
        "validated": True,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": fm.get("schema_version", "1.0"),
    }
    updated = update_frontmatter(page.content, fm_updates)
    repo.upsert_page(page_id, updated)
    return {"page_id": page_id, "validated": True, "validated_at": fm_updates["validated_at"]}
