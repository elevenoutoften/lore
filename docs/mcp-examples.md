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
      "resources": {"subscribe": false, "listChanged": false},
      "prompts": {"listChanged": false}
    },
    "serverInfo": {
      "name": "lore",
      "version": "0.3.0b1"
    },
    "instructions": "tools/list advertises Lore's six core tools only. Call the well-known lore_overview tool via tools/call to load the complete runtime tool index and advanced schemas on demand."
  }
}
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

`tools/list` advertises only the six core tools. Each entry carries its
`description`, an `inputSchema` with per-field descriptions stripped, and
read/write `annotations`. The remaining tools (~53 at last count) are not
listed here; discover and call them via the well-known `lore_overview` tool
(`tools/call`), which returns the full runtime tool index on demand.

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "tools": [
      {
        "name": "lore_capture",
        "description": "Canonical durable agent-memory write. Creates a reviewable draft intake artifact, then runs the shared indexing and consolidation loop. Actor identity is always derived from the authenticated caller.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "observation": {"type": "string"},
            "title": {"type": "string"},
            "namespace": {"type": "string", "enum": ["inbox", "notes"], "default": "inbox"},
            "agent": {"type": "string"},
            "capture_date": {"type": "string"},
            "source_task": {"type": "string"},
            "task_id": {"type": "string"},
            "decision_id": {"type": "string"},
            "trace_id": {"type": "string"},
            "tool_calls": {"type": "array", "items": {"type": "object"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "policies_applied": {"type": "array", "items": {"type": "string"}},
            "related_pages": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string"},
            "epistemic_status": {"type": "string", "enum": ["operator_declared", "retrieved", "inferred", "assumption"]},
            "suggested_target_page": {"type": "string"},
            "provenance": {"type": "object"},
            "lane": {"type": "string", "enum": ["project", "procedural", "ops", "companion", "draft"]}
          },
          "required": ["observation"]
        },
        "annotations": {"readOnlyHint": false, "destructiveHint": true}
      },
      {
        "name": "lore_recall",
        "description": "Recency- and salience-weighted recall over the durable claim ledger. Ranks active memory by strength (reinforce/decay), recency, recall frequency, and -- when a query is given -- lexical relevance. Returns each claim's score breakdown.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": {"type": "string"},
            "subject": {"type": "string"},
            "lane": {"type": "string", "enum": ["project", "procedural", "ops", "companion", "draft"]},
            "actor": {"type": "string"},
            "min_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "record_access": {"type": "boolean", "default": false},
            "cross_actor": {"type": "boolean", "default": false}
          }
        },
        "annotations": {"readOnlyHint": true, "destructiveHint": false}
      },
      {
        "name": "lore_ack_recall",
        "description": "Explicitly acknowledge that recalled claims were used, incrementing access_count and last_accessed_at for salience without affecting recency or decay age.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "candidate_ids": {"type": "array", "items": {"type": "string"}},
            "actor": {"type": "string"},
            "cross_actor": {"type": "boolean", "default": false}
          },
          "required": ["candidate_ids"]
        },
        "annotations": {"readOnlyHint": false, "destructiveHint": true}
      },
      {
        "name": "lore_search",
        "description": "Search Lore pages. Returns ranked results with snippets when FTS is available.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string"},
            "visibility": {"type": "string"},
            "status": {"type": "string"},
            "namespace": {"type": "string"},
            "lane": {"type": "string", "enum": ["project", "procedural", "ops", "companion", "draft"]},
            "actor": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
          },
          "required": ["query"]
        },
        "annotations": {"readOnlyHint": true, "destructiveHint": false}
      },
      {
        "name": "lore_read_page",
        "description": "Read a Markdown page from Lore.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "page_id": {"type": "string"}
          },
          "required": ["page_id"]
        },
        "annotations": {"readOnlyHint": true, "destructiveHint": false}
      },
      {
        "name": "lore_upsert_page",
        "description": "Create or replace a Markdown page in Lore.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "page_id": {"type": "string"},
            "content": {"type": "string"}
          },
          "required": ["page_id", "content"]
        },
        "annotations": {"readOnlyHint": false, "destructiveHint": true}
      }
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

This is the canonical durable agent-memory write. It creates a reviewable draft
artifact and runs the shared indexing/consolidation loop. Put all source and
evidence data in the typed `provenance` object. Actor is server-owned and is not
a writable tool input.

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
      "provenance": {
        "source_paths": ["src/gateway/app.py"],
        "evidence": "Verified in the gateway source."
      }
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

## Tool: `lore_create_trace` → `lore_capture` Linked Flow

Create the trace first so the returned `trace_id` can be attached to the
capture.

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 19,
  "method": "tools/call",
  "params": {
    "name": "lore_create_trace",
    "arguments": {
      "actor": "nyx",
      "reason_summary": "Observed that the deployment config was missing FLOW_SESSION_SECRET.",
      "context_refs": [
        {"type": "page", "id": "services/flow"},
        {"type": "task", "id": "flow_000643"}
      ],
      "tool_refs": [
        {"tool": "lore_search", "action": "query", "result_summary": "Found session auth docs."}
      ],
      "outcome": "missing-session-secret"
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 19,
  "result": {
    "content": [{"type": "text", "text": "Created reasoning trace: trace-abc123def456"}],
    "structuredContent": {
      "trace_id": "trace-abc123def456",
      "actor": "nyx",
      "reason_summary": "Observed that the deployment config was missing FLOW_SESSION_SECRET.",
      "status": "active",
      "context_refs": [
        {"type": "page", "id": "services/flow"},
        {"type": "task", "id": "flow_000643"}
      ],
      "tool_refs": [
        {"tool": "lore_search", "action": "query", "result_summary": "Found session auth docs."}
      ],
      "constraints": [],
      "policy_refs": [],
      "alternatives": [],
      "outcome": "missing-session-secret",
      "related_ids": {},
      "created_at": "2026-05-22T10:15:00+00:00",
      "updated_at": "2026-05-22T10:15:00+00:00"
    },
    "isError": false
  }
}
```

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 20,
  "method": "tools/call",
  "params": {
    "name": "lore_capture",
    "arguments": {
      "title": "Flow session auth missing secret",
      "observation": "Flow session auth is missing FLOW_SESSION_SECRET in production.",
      "capture_date": "2026-05-22",
      "related_pages": ["services/flow"],
      "trace_id": "trace-abc123def456",
      "provenance": {"sources": ["agent:nyx"]}
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 20,
  "result": {
    "content": [{"type": "text", "text": "Captured Lore memory: inbox/2026-05-22/flow-session-auth-missing-secret"}],
    "structuredContent": {
      "page": {
        "id": "inbox/2026-05-22/flow-session-auth-missing-secret",
        "title": "Flow session auth missing secret",
        "kind": "capture",
        "visibility": "internal",
        "status": "draft",
        "summary": "Rough agent memory capture; not canonical truth.",
        "tags": ["capture", "agent-memory"],
        "sources": ["agent:nyx"],
        "trace_id": "trace-abc123def456",
        "updated_at": "2026-05-22T10:16:00+00:00",
        "size": 620
      }
    },
    "isError": false
  }
}
```

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 21,
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
  "id": 21,
  "result": {
    "content": [{"type": "text", "text": "1 Lore captures for status: draft.\ninbox/2026-05-22/flow-session-auth-missing-secret - Flow session auth missing secret (draft)"}],
    "structuredContent": {
      "status": "draft",
      "count": 1,
      "captures": [
        {
          "id": "inbox/2026-05-22/flow-session-auth-missing-secret",
          "title": "Flow session auth missing secret",
          "kind": "capture",
          "visibility": "internal",
          "status": "draft",
          "summary": "Rough agent memory capture; not canonical truth.",
          "tags": ["capture", "agent-memory"],
          "sources": ["agent:nyx"],
          "trace_id": "trace-abc123def456",
          "updated_at": "2026-05-22T10:16:00+00:00",
          "size": 620
        }
      ]
    },
    "isError": false
  }
}
```

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 22,
  "method": "tools/call",
  "params": {
    "name": "lore_get_trace",
    "arguments": {
      "trace_id": "trace-abc123def456"
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 22,
  "result": {
    "content": [{"type": "text", "text": "Retrieved reasoning trace: trace-abc123def456"}],
    "structuredContent": {
      "trace_id": "trace-abc123def456",
      "actor": "nyx",
      "reason_summary": "Observed that the deployment config was missing FLOW_SESSION_SECRET.",
      "status": "active",
      "context_refs": [
        {"type": "page", "id": "services/flow"},
        {"type": "task", "id": "flow_000643"}
      ],
      "tool_refs": [
        {"tool": "lore_search", "action": "query", "result_summary": "Found session auth docs."}
      ],
      "constraints": [],
      "policy_refs": [],
      "alternatives": [],
      "outcome": "missing-session-secret",
      "related_ids": {},
      "created_at": "2026-05-22T10:15:00+00:00",
      "updated_at": "2026-05-22T10:15:00+00:00"
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
