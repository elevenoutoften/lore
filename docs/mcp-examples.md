# Lore MCP Examples

Endpoint: `POST http://localhost:8078/mcp`

## `initialize`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {}
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-11-25",
    "capabilities": {
      "tools": {"listChanged": false},
      "resources": {"subscribe": false, "listChanged": false}
    },
    "serverInfo": {
      "name": "axis-lore",
      "version": "0.2.0"
    }
  }
}
```
```

## `ping`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "ping",
  "params": {}
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {}
}
```

## `tools/list`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {}
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "tools": [
      {"name": "lore_list_pages", "title": "List Lore Pages"},
      {"name": "lore_read_page", "title": "Read Lore Page"},
      {"name": "lore_search", "title": "Search Lore"},
      {"name": "lore_link_graph", "title": "Lore Link Graph"},
      {"name": "lore_page_links", "title": "Lore Page Links"},
      {"name": "lore_lint", "title": "Lint Lore"},
      {"name": "lore_capture", "title": "Capture Lore Memory"},
      {"name": "lore_list_captures", "title": "List Lore Captures"},
      {"name": "lore_upsert_page", "title": "Upsert Lore Page"}
    ]
  }
}
```

## Tool: `lore_list_pages`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "tools/call",
  "params": {
    "name": "lore_list_pages",
    "arguments": {
      "kind": "service",
      "limit": 2
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "result": {
    "content": [{"type": "text", "text": "services/workflow-engine - Flow (service, internal)\nservices/lore - Lore (service, internal)"}],
    "structuredContent": {
      "pages": [
        {"id": "services/workflow-engine", "title": "Workflow Engine", "kind": "service", "visibility": "internal", "status": "active", "summary": "Agent-first task board and API.", "tags": [], "sources": [], "updated_at": "2026-05-01T12:00:00+00:00", "size": 180},
        {"id": "services/lore", "title": "Lore", "kind": "service", "visibility": "internal", "status": "active", "summary": "Markdown-backed LLM wiki.", "tags": ["wiki"], "sources": ["docs/10-lore-llm-wiki-plan.md"], "updated_at": "2026-05-01T12:05:00+00:00", "size": 640}
      ]
    },
    "isError": false
  }
}
```

## Tool: `lore_read_page`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 11,
  "method": "tools/call",
  "params": {
    "name": "lore_read_page",
    "arguments": {
      "page_id": "services/lore"
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 11,
  "result": {
    "content": [{"type": "text", "text": "---\ntitle: Lore\nkind: service\nvisibility: internal\n---\n\n# Lore\n\nMarkdown-backed project registry.\n"}],
    "structuredContent": {
      "page": {
        "id": "services/lore",
        "title": "Lore",
        "kind": "service",
        "visibility": "internal",
        "status": null,
        "summary": null,
        "tags": [],
        "sources": [],
        "updated_at": "2026-05-01T12:05:00+00:00",
        "size": 120,
        "content": "---\ntitle: Lore\nkind: service\nvisibility: internal\n---\n\n# Lore\n\nMarkdown-backed project registry.\n",
        "body": "# Lore\n\nMarkdown-backed project registry.\n",
        "frontmatter": {"title": "Lore", "kind": "service", "visibility": "internal"}
      }
    },
    "isError": false
  }
}
```

## Tool: `lore_search`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 12,
  "method": "tools/call",
  "params": {
    "name": "lore_search",
    "arguments": {
      "query": "workflow-engine",
      "limit": 5
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 12,
  "result": {
    "content": [{"type": "text", "text": "services/workflow-engine - Workflow Engine (score 20)\n  Workflow Engine exposes curated image workflows through the gateway."}],
    "structuredContent": {
      "query": "workflow-engine",
      "hits": [
        {
          "page": {"id": "services/workflow-engine", "title": "Workflow Engine", "kind": "service", "visibility": "internal", "status": "active", "summary": "Image workflow gateway for Axis apps.", "tags": ["image"], "sources": ["docs/08-workflow-engine-workflow-ui-plan.md"], "updated_at": "2026-05-01T12:12:00+00:00", "size": 580},
          "score": 20,
          "matches": ["Workflow Engine exposes curated image workflows through the gateway."]
        }
      ]
    },
    "isError": false
  }
}
```

## Tool: `lore_link_graph`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 13,
  "method": "tools/call",
  "params": {
    "name": "lore_link_graph",
    "arguments": {}
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 13,
  "result": {
    "content": [{"type": "text", "text": "2 pages, 1 links, 0 broken internal links."}],
    "structuredContent": {
      "pages": [
        {"id": "projects/operations-hub", "title": "Operations Hub", "kind": "project", "visibility": "internal", "status": "active", "summary": "Internal operations workspace.", "tags": ["ops"], "sources": ["README.md"], "updated_at": "2026-05-01T12:00:00+00:00", "size": 420},
        {"id": "services/lore", "title": "Lore", "kind": "service", "visibility": "internal", "status": "active", "summary": "Markdown-backed LLM wiki.", "tags": ["wiki"], "sources": [], "updated_at": "2026-05-01T12:05:00+00:00", "size": 640}
      ],
      "links": [
        {"source": "projects/operations-hub", "source_title": "Operations Hub", "target": "services/lore", "target_title": "Lore", "href": "/services/lore", "label": "Lore", "exists": true, "external": false}
      ],
      "broken_links": []
    },
    "isError": false
  }
}
```

## Tool: `lore_page_links`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 14,
  "method": "tools/call",
  "params": {
    "name": "lore_page_links",
    "arguments": {
      "page_id": "services/lore"
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 14,
  "result": {
    "content": [{"type": "text", "text": "services/lore - Lore\n1 backlinks, 0 outgoing links, 0 broken internal links.\nbacklink: projects/operations-hub - Operations Hub"}],
    "structuredContent": {
      "page": {"id": "services/lore", "title": "Lore", "kind": "service", "visibility": "internal", "status": "active", "summary": "Markdown-backed LLM wiki.", "tags": ["wiki"], "sources": [], "updated_at": "2026-05-01T12:05:00+00:00", "size": 640},
      "outgoing": [],
      "backlinks": [
        {"source": "projects/operations-hub", "source_title": "Operations Hub", "target": "services/lore", "target_title": "Lore", "href": "/services/lore", "label": "Lore", "exists": true, "external": false}
      ],
      "missing_links": []
    },
    "isError": false
  }
}
```

## Tool: `lore_lint`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 15,
  "method": "tools/call",
  "params": {
    "name": "lore_lint",
    "arguments": {}
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 15,
  "result": {
    "content": [{"type": "text", "text": "12 pages checked, 1 lint issues.\nwarning: services/workflow-engine: Frontmatter is missing sources."}],
    "structuredContent": {
      "checked_pages": 12,
      "issue_count": 1,
      "issues": [
        {"rule": "missing_sources", "severity": "warning", "page_id": "services/workflow-engine", "title": "Workflow Engine", "message": "Frontmatter is missing sources.", "target": null, "detail": null}
      ]
    },
    "isError": false
  }
}
```

## Tool: `lore_capture`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 16,
  "method": "tools/call",
  "params": {
    "name": "lore_capture",
    "arguments": {
      "title": "Workflow Engine queue note",
      "observation": "Workflow Engine queue retries should be documented before changing gateway behavior.",
      "capture_date": "2026-05-01",
      "related_pages": ["services/workflow-engine"],
      "confidence": "medium",
      "sources": ["src/gateway/app.py"]
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 16,
  "result": {
    "content": [{"type": "text", "text": "Captured Lore memory: inbox/2026-05-01/workflow-engine-queue-note"}],
    "structuredContent": {
      "page": {
        "id": "inbox/2026-05-01/workflow-engine-queue-note",
        "title": "Workflow Engine queue note",
        "kind": "capture",
        "visibility": "internal",
        "status": "draft",
        "summary": "Rough agent memory capture; not canonical truth.",
        "tags": ["capture", "agent-memory"],
        "sources": ["src/gateway/app.py"],
        "updated_at": "2026-05-01T12:20:00+00:00",
        "size": 520,
        "content": "---\ntitle: Workflow Engine queue note\nkind: capture\nvisibility: internal\nstatus: draft\nsummary: Rough agent memory capture; not canonical truth.\n---\n\n# Workflow Engine queue note\n\n## Observation\n\nWorkflow Engine queue retries should be documented before changing gateway behavior.\n",
        "body": "# Workflow Engine queue note\n\n## Observation\n\nWorkflow Engine queue retries should be documented before changing gateway behavior.\n",
        "frontmatter": {"title": "Workflow Engine queue note", "kind": "capture", "visibility": "internal", "status": "draft", "summary": "Rough agent memory capture; not canonical truth."}
      }
    },
    "isError": false
  }
}
```

## Tool: `lore_list_captures`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 17,
  "method": "tools/call",
  "params": {
    "name": "lore_list_captures",
    "arguments": {
      "status": "draft",
      "limit": 20
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 17,
  "result": {
    "content": [{"type": "text", "text": "1 Lore captures for status: draft.\ninbox/2026-05-01/workflow-engine-queue-note - Workflow Engine queue note (draft)"}],
    "structuredContent": {
      "status": "draft",
      "count": 1,
      "captures": [
        {"id": "inbox/2026-05-01/workflow-engine-queue-note", "title": "Workflow Engine queue note", "kind": "capture", "visibility": "internal", "status": "draft", "summary": "Rough agent memory capture; not canonical truth.", "tags": ["capture", "agent-memory"], "sources": ["src/gateway/app.py"], "updated_at": "2026-05-01T12:20:00+00:00", "size": 520}
      ]
    },
    "isError": false
  }
}
```

## Tool: `lore_upsert_page`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 18,
  "method": "tools/call",
  "params": {
    "name": "lore_upsert_page",
    "arguments": {
      "page_id": "services/lore",
      "content": "---\ntitle: Lore\nkind: service\nvisibility: internal\n---\n\n# Lore\n\nMarkdown-backed project registry.\n"
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 18,
  "result": {
    "content": [{"type": "text", "text": "Updated Lore page: services/lore"}],
    "structuredContent": {
      "page": {
        "id": "services/lore",
        "title": "Lore",
        "kind": "service",
        "visibility": "internal",
        "status": null,
        "summary": null,
        "tags": [],
        "sources": [],
        "updated_at": "2026-05-01T12:25:00+00:00",
        "size": 120,
        "content": "---\ntitle: Lore\nkind: service\nvisibility: internal\n---\n\n# Lore\n\nMarkdown-backed project registry.\n",
        "body": "# Lore\n\nMarkdown-backed project registry.\n",
        "frontmatter": {"title": "Lore", "kind": "service", "visibility": "internal"}
      }
    },
    "isError": false
  }
}
```

## `resources/list`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 30,
  "method": "resources/list",
  "params": {}
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 30,
  "result": {
    "resources": [
      {
        "uri": "lore://pages/services/lore",
        "name": "services/lore",
        "title": "Lore",
        "description": "Markdown-backed LLM wiki.",
        "mimeType": "text/markdown",
        "size": 640,
        "annotations": {
          "audience": ["assistant"],
          "lastModified": "2026-05-01T12:05:00+00:00"
        }
      }
    ]
  }
}
```

## `resources/read`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 31,
  "method": "resources/read",
  "params": {
    "uri": "lore://pages/services/lore"
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 31,
  "result": {
    "contents": [
      {
        "uri": "lore://pages/services/lore",
        "mimeType": "text/markdown",
        "text": "---\ntitle: Lore\nkind: service\nvisibility: internal\n---\n\n# Lore\n\nMarkdown-backed project registry.\n"
      }
    ]
  }
}
```

## `resources/templates/list`

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 32,
  "method": "resources/templates/list",
  "params": {}
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 32,
  "result": {
    "resourceTemplates": [
      {
        "uriTemplate": "lore://pages/{page_id}",
        "name": "Lore page",
        "title": "Lore Page",
        "description": "Read a Markdown page from the Lore LLM wiki.",
        "mimeType": "text/markdown"
      }
    ]
  }
}
