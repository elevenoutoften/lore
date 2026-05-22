# Lore SDK

Stdlib-only Python client for the Lore REST API.

## Installation

From this repository:

```sh
cd sdk/python
python -m pip install -e .
```

You can also copy the `lore_sdk` package into another Python 3.11+ project. The SDK has no third-party dependencies.

## Basic Usage

```python
from lore_sdk import LoreClient

client = LoreClient("http://localhost:8078")

print(client.health())
pages = client.list_pages(kind="project")
page = client.get_page("projects/example-project")
results = client.search("ComfyUI gateway", limit=10)
```

## Writes

```python
client.upsert_page(
    "services/lore",
    """---
title: Lore
kind: service
visibility: internal
---

# Lore
""",
)

client.create_capture(
    title="Deployment note",
    body="Observed a new deployment step.",
    source="runbook.md",
    tags=["deploy"],
)
```

## Trace-Linked Capture

Use `create_trace` to record rationale, then attach the returned `trace_id` to
`create_capture` so the observation and trace can be read back together.

```python
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

## Auth

Pass a bearer token when your Lore server requires authentication:

```python
client = LoreClient("http://localhost:8078", auth_token="your-token")
```

## Error Handling

Non-2xx responses raise `LoreError`.

```python
from lore_sdk import LoreClient, LoreError

client = LoreClient("http://localhost:8078")

try:
    client.get_page("missing/page")
except LoreError as exc:
    print(exc.status_code)
    print(exc.message)
```
