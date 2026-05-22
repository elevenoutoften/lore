# Reasoning Trace Protocol

## Purpose
Reasoning traces store audit-grade rationale summaries for agent decisions. They explain *why* a choice was made, NOT the raw chain-of-thought.

## What NOT to Store
- Raw model chain-of-thought or hidden reasoning
- Verbatim model outputs
- Internal monologue or scratchpad content

## What TO Store
- Concise rationale summaries (human-readable, <5000 chars)
- Context references (pages, captures, tasks examined)
- Tool references (what tools were called, what they returned)
- Constraints that applied
- Policy references that governed the decision
- Alternatives considered and why they were rejected
- Outcome of the decision

## MCP Tool Examples

### Create a trace linked to a Flow task

```json
{
  "name": "lore_create_trace",
  "arguments": {
    "actor": "nyx",
    "reason_summary": "Selected low-risk patch over full rewrite due to active users depending on current API shape.",
    "context_refs": [{"type": "page", "id": "services/lore"}, {"type": "task", "id": "flow_000580"}],
    "tool_refs": [{"tool": "lore_search", "action": "query", "result_summary": "Found 3 pages referencing the old API."}],
    "constraints": ["Preserve backward compatibility."],
    "policy_refs": ["L-TRACE-01"],
    "alternatives": [{"description": "Full rewrite of the extraction pipeline.", "rejected_reason": "Too risky for production traffic."}],
    "outcome": "Applied low-risk patch.",
    "related_ids": {"task_id": "flow_000580", "page_id": "services/lore"}
  }
}
```

### Query traces for a task

```json
{
  "name": "lore_list_traces",
  "arguments": {"task_id": "flow_000580", "limit": 10}
}
```

## SDK Examples

```python
from lore_sdk import LoreClient

client = LoreClient("http://localhost:8078")

trace = client.create_trace(
    actor="nyx",
    reason_summary="Selected low-risk patch over full rewrite due to active users depending on current API shape.",
    context_refs=[{"type": "page", "id": "services/lore"}],
    related_ids={"task_id": "flow_000580", "page_id": "services/lore"},
)

client.get_trace(trace["trace_id"])
client.list_traces(actor="nyx", task_id="flow_000580", limit=10)
```

## Trace-Linked Capture Flow

This flow links a reasoning trace to a capture so agents can read back the
decision context alongside the captured observation.

### MCP JSON-RPC Flow

1. Create a trace with `lore_create_trace`:

```json
{
  "jsonrpc": "2.0",
  "id": 21,
  "method": "tools/call",
  "params": {
    "name": "lore_create_trace",
    "arguments": {
      "actor": "nyx",
      "reason_summary": "Observed that the deployment config was missing FLOW_SESSION_SECRET.",
      "context_refs": [{"type": "page", "id": "services/flow"}],
      "tool_refs": [{"tool": "lore_search", "action": "query", "result_summary": "Found session auth docs."}],
      "outcome": "missing-session-secret"
    }
  }
}
```

Response excerpt:

```json
{
  "result": {
    "structuredContent": {
      "trace_id": "trace-abc123def456",
      "actor": "nyx",
      "reason_summary": "Observed that the deployment config was missing FLOW_SESSION_SECRET.",
      "context_refs": [{"type": "page", "id": "services/flow"}],
      "tool_refs": [{"tool": "lore_search", "action": "query", "result_summary": "Found session auth docs."}],
      "outcome": "missing-session-secret"
    }
  }
}
```

2. Pass that `trace_id` into `lore_capture`:

```json
{
  "jsonrpc": "2.0",
  "id": 22,
  "method": "tools/call",
  "params": {
    "name": "lore_capture",
    "arguments": {
      "title": "Flow session auth missing secret",
      "observation": "Flow session auth is missing FLOW_SESSION_SECRET in production.",
      "capture_date": "2026-05-22",
      "related_pages": ["services/flow"],
      "trace_id": "trace-abc123def456",
      "sources": ["agent:nyx"]
    }
  }
}
```

3. Read back captures with `lore_list_captures` and match the returned
   `trace_id`:

```json
{
  "jsonrpc": "2.0",
  "id": 23,
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

Response excerpt:

```json
{
  "result": {
    "structuredContent": {
      "captures": [
        {
          "id": "inbox/2026-05-22/flow-session-auth-missing-secret",
          "title": "Flow session auth missing secret",
          "status": "draft",
          "trace_id": "trace-abc123def456",
          "sources": ["agent:nyx"]
        }
      ]
    }
  }
}
```

4. Read the linked capture page with `lore_read_page`:

```json
{
  "jsonrpc": "2.0",
  "id": 24,
  "method": "tools/call",
  "params": {
    "name": "lore_read_page",
    "arguments": {
      "page_id": "inbox/2026-05-22/flow-session-auth-missing-secret"
    }
  }
}
```

5. Read the original trace with `lore_get_trace`:

```json
{
  "jsonrpc": "2.0",
  "id": 25,
  "method": "tools/call",
  "params": {
    "name": "lore_get_trace",
    "arguments": {
      "trace_id": "trace-abc123def456"
    }
  }
}
```

### Python SDK Flow

Use `create_trace` to establish the reasoning record, `create_capture` to
attach the returned `trace_id`, then read back the capture and trace:

```python
from lore_sdk import LoreClient

client = LoreClient("http://localhost:8078")

trace = client.create_trace(
    actor="nyx",
    reason_summary="Observed that the deployment config was missing FLOW_SESSION_SECRET.",
    context_refs=[{"type": "page", "id": "services/flow"}],
    tool_refs=[{"tool": "lore_search", "action": "query", "result_summary": "Found session auth docs."}],
    outcome="missing-session-secret",
)

capture = client.create_capture(
    title="Flow session auth missing secret",
    body="Flow session auth is missing FLOW_SESSION_SECRET in production.",
    source="agent:nyx",
    trace_id=trace["trace_id"],
    capture_date="2026-05-22",
    related_pages=["services/flow"],
)

captured = client.list_captures(status="draft")
linked_capture = next(
    item for item in captured["captures"] if item.get("trace_id") == trace["trace_id"]
)
capture_page = client.get_page(linked_capture["id"])
linked_trace = client.get_trace(trace["trace_id"])
recent_traces = client.list_traces(actor="nyx", limit=10)
```
