# Lore API Examples

Base URL: `http://localhost:8078`

## `GET /healthz`

```bash
curl http://localhost:8078/healthz
```

```json
{
  "ok": true
}
```

## `GET /api/version`

```bash
curl http://localhost:8078/api/version
```

```json
{
  "name": "lore",
  "version": "0.3.0b1",
  "python_version": "3.12.5",
  "api_version": "1"
}
```

## `GET /api/pages`

```bash
curl http://localhost:8078/api/pages
```

```json
[
  {
    "id": "projects/operations-hub",
    "title": "Operations Hub",
    "kind": "project",
    "visibility": "internal",
    "status": "active",
    "summary": "Internal operations workspace for agents and maintainers.",
    "tags": ["ops", "automation"],
    "sources": ["README.md"],
    "updated_at": "2026-05-01T12:00:00+00:00",
    "size": 420
  },
  {
    "id": "services/lore",
    "title": "Lore",
    "kind": "service",
    "visibility": "internal",
    "status": "active",
    "summary": "Markdown-backed LLM wiki and project registry.",
    "tags": ["wiki", "mcp"],
    "sources": ["docs/10-lore-llm-wiki-plan.md"],
    "updated_at": "2026-05-01T12:05:00+00:00",
    "size": 640
  }
]
```

## `GET /api/pages?kind=service`

```bash
curl "http://localhost:8078/api/pages?kind=service"
```

```json
[
  {
    "id": "services/lore",
    "title": "Lore",
    "kind": "service",
    "visibility": "internal",
    "status": "active",
    "summary": "Markdown-backed LLM wiki and project registry.",
    "tags": ["wiki", "mcp"],
    "sources": ["docs/10-lore-llm-wiki-plan.md"],
    "updated_at": "2026-05-01T12:05:00+00:00",
    "size": 640
  }
]
```

## `GET /api/pages/{page_id}`

```bash
curl http://localhost:8078/api/pages/services/lore
```

```json
{
  "id": "services/lore",
  "title": "Lore",
  "kind": "service",
  "visibility": "internal",
  "status": "active",
  "summary": "Markdown-backed LLM wiki and project registry.",
  "tags": ["wiki", "mcp"],
  "sources": ["docs/10-lore-llm-wiki-plan.md"],
  "updated_at": "2026-05-01T12:05:00+00:00",
  "size": 640,
  "content": "---\ntitle: Lore\nkind: service\nvisibility: internal\nstatus: active\nsummary: Markdown-backed LLM wiki and project registry.\ntags: [wiki, mcp]\nsources:\n  - docs/10-lore-llm-wiki-plan.md\n---\n\n# Lore\n\nLore stores Markdown pages for humans and agents.\n",
  "body": "# Lore\n\nLore stores Markdown pages for humans and agents.\n",
  "frontmatter": {
    "title": "Lore",
    "kind": "service",
    "visibility": "internal",
    "status": "active",
    "summary": "Markdown-backed LLM wiki and project registry.",
    "tags": ["wiki", "mcp"],
    "sources": ["docs/10-lore-llm-wiki-plan.md"]
  }
}
```

## `GET /api/pages/{page_id}/rendered`

```bash
curl http://localhost:8078/api/pages/services/lore/rendered
```

```json
{
  "id": "services/lore",
  "title": "Lore",
  "kind": "service",
  "visibility": "internal",
  "status": "active",
  "summary": "Markdown-backed LLM wiki and project registry.",
  "tags": ["wiki", "mcp"],
  "sources": ["docs/10-lore-llm-wiki-plan.md"],
  "updated_at": "2026-05-01T12:05:00+00:00",
  "size": 640,
  "html": "<p>Lore stores Markdown pages for humans and agents.</p>\n",
  "toc": [],
  "links": [],
  "missing_links": []
}
```

## `GET /api/pages/{page_id}/links`

```bash
curl http://localhost:8078/api/pages/services/lore/links
```

```json
{
  "page": {
    "id": "services/lore",
    "title": "Lore",
    "kind": "service",
    "visibility": "internal",
    "status": "active",
    "summary": "Markdown-backed LLM wiki and project registry.",
    "tags": ["wiki", "mcp"],
    "sources": ["docs/10-lore-llm-wiki-plan.md"],
    "updated_at": "2026-05-01T12:05:00+00:00",
    "size": 640
  },
  "outgoing": [
    {
      "source": "services/lore",
      "source_title": "Lore",
      "target": "services/workflow-engine",
      "target_title": "Workflow Engine",
      "href": "/services/workflow-engine",
      "label": "Workflow Engine",
      "exists": true,
      "external": false
    }
  ],
  "backlinks": [],
  "missing_links": []
}
```

## `PUT /api/pages/{page_id}`

```bash
curl -X PUT http://localhost:8078/api/pages/services/lore \
  -H "Content-Type: application/json" \
  -d '{"content":"---\ntitle: Lore\nkind: service\nvisibility: internal\nstatus: active\nsummary: Markdown-backed LLM wiki.\n---\n\n# Lore\n\nUpdated content.\n"}'
```

Request body:

```json
{
  "content": "---\ntitle: Lore\nkind: service\nvisibility: internal\nstatus: active\nsummary: Markdown-backed LLM wiki.\n---\n\n# Lore\n\nUpdated content.\n"
}
```

```json
{
  "id": "services/lore",
  "title": "Lore",
  "kind": "service",
  "visibility": "internal",
  "status": "active",
  "summary": "Markdown-backed LLM wiki.",
  "tags": [],
  "sources": [],
  "updated_at": "2026-05-01T12:10:00+00:00",
  "size": 130,
  "content": "---\ntitle: Lore\nkind: service\nvisibility: internal\nstatus: active\nsummary: Markdown-backed LLM wiki.\n---\n\n# Lore\n\nUpdated content.\n",
  "body": "# Lore\n\nUpdated content.\n",
  "frontmatter": {
    "title": "Lore",
    "kind": "service",
    "visibility": "internal",
    "status": "active",
    "summary": "Markdown-backed LLM wiki."
  }
}
```

## `DELETE /api/pages/{page_id}`

```bash
curl -i -X DELETE http://localhost:8078/api/pages/services/lore
```

```text
HTTP/1.1 204 No Content
```

## `GET /api/search?q=workflow-engine`

```bash
curl "http://localhost:8078/api/search?q=workflow-engine"
```

```json
{
  "query": "workflow-engine",
  "hits": [
    {
      "page": {
        "id": "services/workflow-engine",
        "title": "Workflow Engine",
        "kind": "service",
        "visibility": "internal",
        "status": "active",
        "summary": "Image workflow gateway for Axis apps.",
        "tags": ["image", "workflow"],
        "sources": ["docs/08-workflow-engine-workflow-ui-plan.md"],
        "updated_at": "2026-05-01T12:12:00+00:00",
        "size": 580
      },
      "score": 20,
      "matches": ["Workflow Engine exposes curated image workflows through the gateway."]
    }
  ]
}
```
```

## `GET /api/catalog`

```bash
curl http://localhost:8078/api/catalog
```

```json
{
  "kinds": ["project", "service"],
  "visibilities": ["internal", "public"],
  "tags": ["axis", "gpu", "mcp", "wiki"]
}
```

## `GET /api/links`

```bash
curl http://localhost:8078/api/links
```

```json
{
  "pages": [
    {
      "id": "services/lore",
      "title": "Lore",
      "kind": "service",
      "visibility": "internal",
      "status": "active",
      "summary": "Markdown-backed LLM wiki and project registry.",
      "tags": ["wiki", "mcp"],
      "sources": ["docs/10-lore-llm-wiki-plan.md"],
      "updated_at": "2026-05-01T12:05:00+00:00",
      "size": 640
    }
  ],
  "links": [
    {
      "source": "services/lore",
      "source_title": "Lore",
      "target": "services/workflow-engine",
      "target_title": "Workflow Engine",
      "href": "/services/workflow-engine",
      "label": "Workflow Engine",
      "exists": true,
      "external": false
    }
  ],
  "broken_links": []
}
```

## `GET /api/lint`

```bash
curl http://localhost:8078/api/lint
```

```json
{
  "checked_pages": 12,
  "issue_count": 1,
  "issues": [
    {
      "rule": "missing_sources",
      "severity": "warning",
      "page_id": "services/workflow-engine",
      "title": "Workflow Engine",
      "message": "Frontmatter is missing sources.",
      "target": null,
      "detail": null
    }
  ]
}
```

## `POST /api/memory/capture`

```bash
curl -X POST http://localhost:8078/api/memory/capture \
  -H "Content-Type: application/json" \
  -d '{"text":"Workflow Engine queue retries should be documented before changing gateway behavior.","agent_name":"codex","namespace":"notes","lane":"ops","task_id":"FLOW-105","provenance":{"source_paths":["src/gateway/app.py"]},"metadata":{"title":"Workflow Engine queue note","capture_date":"2026-05-01","related_pages":["services/workflow-engine"],"confidence":"medium","suggested_target_page":"services/workflow-engine"}}'
```

Request body:

```json
{
  "text": "Workflow Engine queue retries should be documented before changing gateway behavior.",
  "agent_name": "codex",
  "namespace": "notes",
  "lane": "ops",
  "task_id": "FLOW-105",
  "provenance": {
    "source_paths": ["src/gateway/app.py"]
  },
  "metadata": {
    "title": "Workflow Engine queue note",
    "capture_date": "2026-05-01",
    "related_pages": ["services/workflow-engine"],
    "confidence": "medium",
    "suggested_target_page": "services/workflow-engine"
  }
}
```

```json
{
  "capture_id": "notes/codex/2026-05-01/workflow-engine-queue-note",
  "timestamp": "2026-05-01T12:20:00+00:00"
}
```

## `GET /api/captures`

```bash
curl http://localhost:8078/api/captures
```

```json
{
  "status": "draft",
  "count": 1,
  "captures": [
    {
      "id": "inbox/2026-05-01/workflow-engine-queue-note",
      "title": "Workflow Engine queue note",
      "kind": "capture",
      "visibility": "internal",
      "status": "draft",
      "summary": "Rough agent memory capture; not canonical truth.",
      "tags": ["capture", "agent-memory"],
      "sources": ["src/gateway/app.py"],
      "updated_at": "2026-05-01T12:20:00+00:00",
      "size": 520
    }
  ]
}
```

## `GET /api/captures?status=all`

```bash
curl "http://localhost:8078/api/captures?status=all"
```

```json
{
  "status": "all",
  "count": 2,
  "captures": [
    {
      "id": "inbox/2026-05-01/workflow-engine-queue-note",
      "title": "Workflow Engine queue note",
      "kind": "capture",
      "visibility": "internal",
      "status": "draft",
      "summary": "Rough agent memory capture; not canonical truth.",
      "tags": ["capture", "agent-memory"],
      "sources": ["src/gateway/app.py"],
      "updated_at": "2026-05-01T12:20:00+00:00",
      "size": 520
    },
    {
      "id": "inbox/2026-04-30/accepted-note",
      "title": "Accepted note",
      "kind": "capture",
      "visibility": "internal",
      "status": "accepted",
      "summary": "Rough agent memory capture; not canonical truth.",
      "tags": ["capture", "agent-memory"],
      "sources": [],
      "updated_at": "2026-04-30T18:00:00+00:00",
      "size": 360
    }
  ]
}
