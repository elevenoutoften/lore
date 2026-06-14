# Agent Memory Contract

Lore is an **agent memory backend** first. This document is the canonical contract
for the memory product surface: how an agent connects with a token, writes durable
memory, and recalls it ranked by relevance, recency, and salience — over both HTTP
and MCP. The browser UI is a minimal human surface; everything here is what agents
actually use.

## Connect: one token, one tenant

Every agent connects with a single bearer token. The token is the tenancy boundary:

- In `api_key` auth mode the token resolves to an **actor** (the key's `name`) and a
  **role** (`admin`, `editor`, or `reader`). Reader tokens are rejected on any
  mutating request (`POST`/`PUT`/`PATCH`/`DELETE`). See [security.md](security.md).
- The resolved actor is attached to the request and used for attribution and for
  actor-scoped reads. Agents never share a token; each agent is its own actor.

```
Authorization: Bearer <lore-api-key>
```

Memory is partitioned by **namespace** and tagged by **actor** and **lane**:

- `inbox/<date>/<slug>` — shared intake, visible to all agents in the workspace.
- `notes/<agent>/<date>/<slug>` — agent-scoped notes namespaced by actor.
- **lanes** (`project`, `procedural`, `ops`, `companion`, `draft`) categorise memory
  by purpose and are first-class filters on capture, search, and recall.

This is the multi-tenant model: one workspace, token-scoped actors, namespace- and
lane-partitioned memory. Hard isolation between unrelated tenants is achieved by
running separate Lore instances (separate content dirs and key databases).

## Write: capture

Captures are the high-frequency write path. Rough agent memory lands as draft
Markdown for later consolidation; it is **not** accepted truth until promoted.

HTTP:

```bash
curl -sS https://lore.example/api/memory/capture \
  -H "Authorization: Bearer $LORE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Pixl renders all text as garbage on Illustrious XL.",
    "agent_name": "pixie",
    "lane": "project",
    "metadata": {"confidence": "high", "source_task": "flow_000770"}
  }'
```

MCP tool: `lore_capture`. Python SDK: `MemoryProvider.capture(...)`.

Captures flow through extraction and consolidation into the **claim ledger** —
subject/predicate/object facts with strength, confidence, provenance, and temporal
bounds. See [consolidation.md](consolidation.md).

### The capture → recall loop

A capture is not immediately recallable: it is first **consolidated** (extracted
into ledger claims, then routed into durable pages). By default Lore runs this
automatically in the background right after each capture (`LORE_AUTO_CONSOLIDATE`,
on by default), so `capture` then `recall` "just works" with no manual step. A
capture with no obvious home still consolidates — it lands on a per-actor
`memory/<actor>` page rather than being stranded.

If you disable auto-consolidation (for very high write volume plus a scheduled
runner), drive it explicitly with `POST /api/consolidation/run` (MCP
`lore_consolidation_run`). Recall is **self-diagnosing**: a `count: 0` response
includes `pending_captures` and a `hint` telling you whether memory is genuinely
absent or just awaiting consolidation.

On a default install with no LLM key (`LORE_LLM_PROVIDER=none`), extraction is
deterministic: it emits coarse `predicate=states` claims keyed off the capture
text rather than fine-grained structured facts. Recall still works; configure an
LLM provider (see [llm-provider-config.md](llm-provider-config.md)) for richer
subject/predicate/object extraction.

## Recall: ranked read

`GET /api/memory/recall` is the agent-facing read surface over the claim ledger. It
ranks live claims (both freshly extracted `candidate` claims and consolidated
`active` ones) by a deterministic, explainable score and returns the breakdown so
an agent can see *why* a claim surfaced.

### Ranking signals

| Signal | Meaning | Source |
| --- | --- | --- |
| **strength** | Reinforce/decay value. Repeated, corroborated claims rise; untouched claims decay. | ledger `strength` (`[0.01, 1.0]`) |
| **recency** | Exponential freshness from the claim's last-access/update anchor. Half-life 30 days. | decay anchor |
| **salience** | How often the claim has been recalled, log-scaled and saturating. | `access_count` |
| **relevance** | Lexical overlap between the query and the claim text. Only mixed in when a query is supplied. | query vs. subject/predicate/object |

Default weights are `strength 0.45, recency 0.25, salience 0.15, relevance 0.15`.
When no query is supplied, the relevance weight is redistributed across the other
three signals (so the weights always sum to 1). The exact weights used for a request
are returned in the response `weights` field.

Recalling a claim **records access**: its `access_count` is incremented and
`last_accessed_at` is stamped. This feeds both the salience signal and the recency
anchor on subsequent recalls — frequently-needed memory stays hot. Pass
`record_access=false` for read-only inspection that must not perturb ranking.

### Example

```bash
curl -sS "https://lore.example/api/memory/recall?query=memory+backend&limit=5" \
  -H "Authorization: Bearer $LORE_API_KEY"
```

```json
{
  "query": "memory backend",
  "count": 5,
  "latency_ms": 9.6,
  "weights": {"strength": 0.45, "recency": 0.25, "salience": 0.15, "relevance": 0.15},
  "claims": [
    {
      "candidate_id": "…",
      "subject": "services/lore",
      "predicate": "is",
      "object": "an agent memory backend",
      "strength": 0.55,
      "access_count": 3,
      "age_days": 1.2,
      "recall_score": 0.71,
      "recall_signals": {"total": 0.71, "strength": 0.55, "recency": 0.97, "salience": 0.46, "relevance": 1.0}
    }
  ]
}
```

Filters: `subject`, `lane`, `actor`, `min_strength`, `valid_at`, `limit`.

MCP tool: `lore_recall` (same parameters and ranking). Python SDK:
`MemoryProvider.recall(query=..., lane=..., actor=...)`.

### Recall vs. search vs. RAG

- **`/api/memory/recall`** — ranked claims from the durable ledger. The default
  "what do I know about X" memory read. Fast, explainable, decay-aware.
- **`/api/search`** — full-text/BM25 over Markdown pages. Document lookup.
- **`/api/rag/retrieve-expanded`** — hybrid retrieval with multi-hop context-graph
  expansion, relevance paths, and supporting/contradicting claims. Use when an agent
  needs assembled context with explanations rather than ranked facts.

## Lifecycle: reinforce, supersede, decay

The ledger is maintained over time:

- **Reinforce** (`POST /api/ledger/reinforce`) — a repeated compatible claim
  strengthens the existing row instead of duplicating it.
- **Supersede** (`POST /api/ledger/supersede`) — a new claim invalidates an old one.
- **Decay** (`POST /api/ledger/decay`) — time-based strength decay (`strength *=
  0.995^days`, floored at `0.01`). Run on a schedule so stale memory fades and recall
  naturally prefers fresh, corroborated facts.

## Performance expectations

Recall is an in-process scored read over the active claim pool. On dev hardware,
p95 recall latency over 500 claims is ~10 ms. The
[`eval/test_recall_latency.py`](../eval/test_recall_latency.py) benchmark enforces a
generous p95 ceiling so regressions are caught, and
[`eval/test_recall_eval.py`](../eval/test_recall_eval.py) enforces recall@3 and MRR
floors on a labelled corpus so ranking quality is held, not just functionality.

## Surface summary

| Operation | HTTP | MCP tool | Python SDK |
| --- | --- | --- | --- |
| Capture memory | `POST /api/memory/capture` | `lore_capture` | `MemoryProvider.capture` |
| Consolidate (auto by default) | `POST /api/consolidation/run` | `lore_consolidation_run` | — |
| Recall ranked claims | `GET /api/memory/recall` | `lore_recall` | `MemoryProvider.recall` |
| Search pages | `GET /api/search` | `lore_search` | `LoreClient.search` |
| Assembled context | `POST /api/rag/retrieve-expanded` | `lore_rag_context_expanded` | — |
| Memory health | `GET /api/memory/health` | `lore_heartbeat_review` | — |
