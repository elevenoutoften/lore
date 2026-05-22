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
