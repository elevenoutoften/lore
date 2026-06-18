from __future__ import annotations

# ruff: noqa: B008
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse

from ..capture import list_captures
from ..code_ingest.ingest_service import ingest_service_code
from ..code_ingest.validate import IngestValidationError, validate_service_id, validate_source_dir
from ..deps import (
    get_audit_log,
    get_code_inventories,
    get_config,
    get_context_graph_cache,
    get_graph_cache,
    get_metrics,
    get_repo,
    get_search_index,
    get_templates,
    get_vector_store,
)
from ..frontmatter import update_frontmatter
from ..link_graph import LinkGraphCache, page_links
from ..markdown_render import render_page_markdown
from ..repository import InvalidPageId, LoreRepository, string_list
from ..route_utils import (
    backlink_groups,
    index_vectors_for_page,
    record_audit,
    require_page,
    template_context,
    validate_content,
    validate_optional_page_id_input,
    validate_page_id_input,
)
from ..schemas import MetadataUpdate, PageDetail, PageRendered, PageSummary, PageUpsert, StubRequest
from .api_keys import require_lore_key_admin

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from ..audit import AuditLog
    from ..config import LoreConfig
    from ..context_graph import ContextGraphCache
    from ..observability import MetricsCollector
    from ..rag.vector_store import VectorStore
    from ..search_index import LoreSearchIndex

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    repo: LoreRepository = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
):
    pages = repo.list_pages(kind=kind, visibility=visibility, q=q)
    catalog = repo.catalog()
    return templates.TemplateResponse(
        request,
        "index.html",
        template_context(
            request,
            pages=pages,
            catalog=catalog,
            selected_page=None,
            query=q or "",
            kind=kind or "",
            visibility=visibility or "",
            page_count=len(pages),
            title=request.app.state.config.app_name,
        ),
    )


@router.get("/embed", response_class=HTMLResponse)
def embed(
    request: Request,
    mode: Literal["page", "search", "capture"] = Query(default="page"),
    pageId: str | None = Query(default=None),
    q: str | None = Query(default=None),
    theme: Literal["light", "dark", "auto"] = Query(default="auto"),
    hideChrome: bool = Query(default=True),
    repo: LoreRepository = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
):
    selected_page = None
    rendered_page = None
    search_results = []
    captures = []

    if mode == "page":
        if not pageId:
            raise HTTPException(status_code=422, detail="pageId is required for page embeds.")
        selected_page = require_page(repo, pageId)
        rendered_page = render_page_markdown(
            selected_page,
            page_ids={summary.id for summary in repo.list_pages()},
        )
    elif mode == "search":
        search_query = (q or "").strip()
        if search_query:
            search_results = repo.search(search_query, limit=20).hits
    elif mode == "capture":
        capture_list = list_captures(repo, status="draft", limit=20)
        captures = capture_list.captures

    return templates.TemplateResponse(
        request,
        "embed.html",
        template_context(
            request,
            title="Embed",
            mode=mode,
            page=selected_page,
            rendered_page=rendered_page,
            query=q or "",
            search_results=search_results,
            captures=captures,
            theme=theme,
            hide_chrome=hideChrome,
        ),
    )


@router.get("/api/pages", response_model=list[PageSummary])
def api_list_pages(
    response: Response,
    kind: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    repo: LoreRepository = Depends(get_repo),
):
    pages, total = repo.list_pages_meta_with_count(kind=kind, visibility=visibility, q=q, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return pages


@router.get("/api/code-references/{code_path:path}")
def api_code_references(code_path: str, repo: LoreRepository = Depends(get_repo)):
    referencing = []
    for page in repo.iter_pages():
        if any(code_path in source for source in page.sources):
            referencing.append(
                {"page": page.model_dump(exclude={"content", "body", "frontmatter"}), "match_field": "sources"}
            )
            continue
        source_paths = string_list(page.frontmatter.get("source_paths"))
        if any(code_path in source_path for source_path in source_paths):
            referencing.append(
                {"page": page.model_dump(exclude={"content", "body", "frontmatter"}), "match_field": "source_paths"}
            )
            continue
        if code_path in page.body:
            referencing.append(
                {"page": page.model_dump(exclude={"content", "body", "frontmatter"}), "match_field": "body"}
            )
    return {"code_path": code_path, "referenced_by": referencing}


@router.post("/api/code-ingest/{service_id:path}")
def api_ingest_service(
    request: Request,
    service_id: str,
    source_dir: str = Query(...),
    code_inventories: dict[str, Any] = Depends(get_code_inventories),
    config: LoreConfig = Depends(get_config),
    _admin: None = Depends(require_lore_key_admin),
):
    # Validate service_id
    try:
        validate_service_id(service_id)
    except IngestValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Validate source_dir against configured roots and limits
    try:
        validated_dir = validate_source_dir(source_dir, config)
    except IngestValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    inventory = ingest_service_code(service_id, validated_dir)
    payload = inventory.model_dump()
    code_inventories[service_id] = payload
    return payload


@router.get("/api/code-ingest/{service_id:path}/inventory")
def api_get_inventory(
    service_id: str,
    code_inventories: dict[str, Any] = Depends(get_code_inventories),
    _admin: None = Depends(require_lore_key_admin),
):
    # Inventories live in memory only and are lost on restart; the previous
    # page.frontmatter fallback was dead code (nothing ever wrote that key).
    if service_id not in code_inventories:
        raise HTTPException(
            status_code=404,
            detail=(
                "No code inventory in memory. Inventories are not persisted across restarts; "
                "re-run POST /api/code-ingest/{service_id}."
            ),
        )
    return code_inventories[service_id]


@router.get("/api/decisions", response_model=list[PageSummary])
def api_list_decisions(repo: LoreRepository = Depends(get_repo)):
    return repo.list_pages(kind="decision")


@router.get("/api/decisions/template")
def api_decision_template():
    return {"content": decision_template()}


@router.get("/api/procedures", response_model=list[PageSummary])
def api_list_procedures(repo: LoreRepository = Depends(get_repo)):
    return repo.list_pages(kind="procedure")


@router.get("/api/procedures/template")
def api_procedure_template():
    return {"content": procedure_template()}


@router.get("/api/procedures/{page_id:path}/export")
def api_export_procedure(page_id: str, repo: LoreRepository = Depends(get_repo)):
    page = require_page(repo, page_id)
    if page.kind != "procedure":
        raise HTTPException(status_code=404, detail="Not a procedure page.")
    return {"content": export_procedure_skill(page), "page_id": page.id}


@router.get("/api/procedures/{page_id:path}", response_model=PageDetail)
def api_read_procedure(page_id: str, repo: LoreRepository = Depends(get_repo)):
    page = require_page(repo, page_id)
    if page.kind != "procedure":
        raise HTTPException(status_code=404, detail="Not a procedure page.")
    return page


@router.get("/api/pages/{page_id:path}/rendered", response_model=PageRendered)
def api_rendered_page(page_id: str, repo: LoreRepository = Depends(get_repo)):
    page = require_page(repo, page_id)
    rendered = render_page_markdown(page, page_ids={summary.id for summary in repo.list_pages()})
    return PageRendered(
        **page.model_dump(exclude={"content", "body", "frontmatter"}),
        html=rendered.html,
        toc=rendered.toc,
        links=rendered.links,
        missing_links=rendered.missing_links,
    )


@router.get("/api/pages/{page_id:path}/history")
def api_page_history(page_id: str, audit_log: AuditLog = Depends(get_audit_log)):
    return [asdict(entry) for entry in audit_log.page_history(page_id)]


@router.patch("/api/pages/{page_id:path}/metadata", response_model=PageDetail)
def api_update_metadata(
    page_id: str,
    payload: MetadataUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
    audit_log: AuditLog = Depends(get_audit_log),
):
    validate_page_id_input(page_id)
    page = require_page(repo, page_id)
    updates = payload.model_dump(exclude_none=True)
    updated_content = update_frontmatter(page.content, updates)
    try:
        updated_page = repo.upsert_page(page.id, updated_content)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    search_idx.upsert_page_from_detail(updated_page)
    index_vectors_for_page(vector_store, updated_page)
    graph_cache.invalidate()
    context_graph_cache.invalidate()
    before_size = len(page.content.encode("utf-8"))
    after_size = len(updated_page.content.encode("utf-8"))
    record_audit(
        request,
        audit_log,
        operation="metadata_update",
        page_id=updated_page.id,
        summary=f"Updated metadata for {updated_page.id}",
        diff_size=abs(after_size - before_size),
    )
    return updated_page


@router.get("/api/pages/{page_id:path}", response_model=PageDetail)
def api_read_page(page_id: str, repo: LoreRepository = Depends(get_repo)):
    return require_page(repo, page_id)


@router.put("/api/pages/{page_id:path}", response_model=PageDetail)
def api_upsert_page(
    page_id: str,
    payload: PageUpsert,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    validate_page_id_input(page_id)
    validate_content(payload.content)
    try:
        before = repo.read_page(page_id)
        page = repo.upsert_page(page_id, payload.content)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if before is None:
        metrics.increment_index_size()
    search_idx.upsert_page_from_detail(page)
    index_vectors_for_page(vector_store, page)
    graph_cache.invalidate()
    context_graph_cache.invalidate()
    before_size = len(before.content.encode("utf-8")) if before is not None else 0
    after_size = len(page.content.encode("utf-8"))
    operation = "update" if before is not None else "create"
    record_audit(
        request,
        audit_log,
        operation=operation,
        page_id=page.id,
        summary=f"{operation.title()} page {page.id}",
        diff_size=abs(after_size - before_size),
    )
    return page


@router.post("/api/pages/{page_id:path}/stub", response_model=PageDetail, status_code=status.HTTP_201_CREATED)
def api_create_stub(
    page_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: StubRequest | None = None,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    validate_page_id_input(page_id)
    try:
        existing = repo.read_page(page_id)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if existing is not None:
        raise HTTPException(status_code=409, detail="Page already exists.")

    payload = payload or StubRequest()
    validate_optional_page_id_input(payload.source_page)
    title = payload.title or page_id.rsplit("/", 1)[-1].replace("-", " ").title()
    source = payload.source_page or "stub-creation"
    stub_content = f"""---
title: {json.dumps(title)}
kind: {payload.kind}
visibility: internal
summary: "Stub page created from broken link."
sources:
  - {source}
status: stub
---

# {title}

This page was auto-created as a stub. Replace with actual content.
"""
    try:
        page = repo.upsert_page(page_id, stub_content)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metrics.increment_index_size()
    search_idx.upsert_page_from_detail(page)
    index_vectors_for_page(vector_store, page)
    graph_cache.invalidate()
    context_graph_cache.invalidate()
    record_audit(
        request,
        audit_log,
        operation="stub_create",
        page_id=page.id,
        summary=f"Created stub page {page.id}",
        diff_size=len(page.content.encode("utf-8")),
    )
    return page


@router.delete("/api/pages/{page_id:path}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_page(
    page_id: str,
    request: Request,
    repo: LoreRepository = Depends(get_repo),
    search_idx: LoreSearchIndex = Depends(get_search_index),
    vector_store: VectorStore = Depends(get_vector_store),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    context_graph_cache: ContextGraphCache = Depends(get_context_graph_cache),
    audit_log: AuditLog = Depends(get_audit_log),
    metrics: MetricsCollector = Depends(get_metrics),
):
    validate_page_id_input(page_id)
    try:
        before = repo.read_page(page_id)
        deleted = repo.delete_page(page_id)
    except InvalidPageId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Lore page not found.")
    metrics.decrement_index_size()
    search_idx.remove_page(page_id)
    vector_store.remove_page(page_id)
    graph_cache.invalidate()
    context_graph_cache.invalidate()
    record_audit(
        request,
        audit_log,
        operation="delete",
        page_id=page_id,
        summary=f"Deleted page {page_id}",
        diff_size=len(before.content.encode("utf-8")) if before is not None else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/pages/{page_id:path}", response_class=HTMLResponse)
def page_alias(
    request: Request,
    page_id: str,
    repo: LoreRepository = Depends(get_repo),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    templates: Jinja2Templates = Depends(get_templates),
):
    return render_reader_page(request, page_id, repo, graph_cache, templates)


@router.get("/{page_id:path}", response_class=HTMLResponse)
def page(
    request: Request,
    page_id: str,
    repo: LoreRepository = Depends(get_repo),
    graph_cache: LinkGraphCache = Depends(get_graph_cache),
    templates: Jinja2Templates = Depends(get_templates),
):
    return render_reader_page(request, page_id, repo, graph_cache, templates)


def render_reader_page(
    request: Request,
    page_id: str,
    repo: LoreRepository,
    graph_cache: LinkGraphCache,
    templates: Jinja2Templates,
):
    try:
        selected_page = require_page(repo, page_id)
    except HTTPException as exc:
        # Browser reader routes: a missing or invalid page id should land on a
        # themed 404 with a way back/search, not a raw JSON error.
        if exc.status_code in (404, 422):
            return templates.TemplateResponse(
                request,
                "not_found.html",
                template_context(request, title="Not found", missing_page_id=page_id),
                status_code=404,
            )
        raise
    pages = repo.list_pages()
    rendered = render_page_markdown(selected_page, page_ids={page.id for page in pages})
    graph = graph_cache.get(repo)
    links = page_links(repo, page_id, graph)
    return templates.TemplateResponse(
        request,
        "index.html",
        template_context(
            request,
            pages=pages,
            catalog=repo.catalog(),
            selected_page=selected_page,
            rendered_page=rendered,
            page_links=links,
            backlink_groups=backlink_groups(links, pages),
            query="",
            kind="",
            visibility="",
            page_count=len(pages),
            title=selected_page.title,
        ),
    )


def decision_template() -> str:
    return """---
title: "Decision Title"
kind: decision
visibility: internal
summary: "One-line summary of the decision."
status: proposed
decided_at: "2026-05-02"
deciders:
  - name
sources:
  - source
alternatives:
  - option A
  - option B
---

# Decision Title

## Context
Why this decision is needed.

## Decision
What was decided.

## Consequences
What changes as a result.
"""


def procedure_template() -> str:
    return """---
title: "Procedure Title"
kind: procedure
visibility: internal
summary: "One-line summary of when to use this procedure."
trigger: "What event or situation starts this procedure."
preconditions:
  - Required state before starting
steps:
  - Step 1: description
  - Step 2: description
  - Step 3: description
postconditions:
  - Expected state after completion
error_handling: "What to do if things go wrong."
schema_version: "1.0"
author: ""
validated: false
validated_at: null
status: draft
---

# Procedure Title

## Trigger
When to use this procedure.

## Steps

1. **Step 1**: Description
2. **Step 2**: Description
3. **Step 3**: Description

## Error Handling
What to do if things go wrong.
"""


def export_procedure_skill(page: PageDetail) -> str:
    steps = page.frontmatter.get("steps", [])
    trigger = page.frontmatter.get("trigger", "")
    preconditions = page.frontmatter.get("preconditions", [])
    postconditions = page.frontmatter.get("postconditions", [])
    error_handling = page.frontmatter.get("error_handling", "")

    return f"""---
name: {page.id.replace("/", "-")}
description: {page.summary or page.title}
---

# {page.title}

{page.summary or ""}

## Trigger
{trigger}

## Preconditions
{chr(10).join(f"- {p}" for p in preconditions) if preconditions else "None"}

## Steps
{chr(10).join(f"{i}. {s}" for i, s in enumerate(steps, 1)) if steps else "None"}

## Postconditions
{chr(10).join(f"- {p}" for p in postconditions) if postconditions else "None"}

## Error Handling
{error_handling or "See page body for details."}

## Source
Exported from [Lore]({page.id}).
"""
