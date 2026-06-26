# Agent Memory Contract

Lore is an **agent memory backend** first. This document is the canonical contract
for the memory product surface: how an agent connects with a token, writes durable
memory, and recalls it ranked by relevance, recency, and salience — over both HTTP
and MCP. The browser UI is a minimal human surface; everything here is what agents
actually use.

## Connect: one token, one tenant

Every agent connects with a single bearer token. The token is the tenancy boundary:

- In `api_key` auth mode the token resolves to an **actor** (the key's `name`) and a
  **role** (`admin`, `writer`, or `reader`). Reader tokens are rejected on any
  mutating request (`POST`/`PUT`/`PATCH`/`DELETE`). See [security.md](security.md).
- The resolved actor is attached to the request and used for attribution and for
  actor-scoped reads. Agents never share a token; each agent is its own actor.
- In `bearer` and `basic` auth modes the single configured secret is treated as an
  admin operator actor. Recall is still scoped to that resolved actor unless the
  admin explicitly requests cross-actor recall.
- In `auth_mode=none`, Lore is single-tenant/shared local memory. There is no API
  key boundary: capture actor/agent is the local request actor (`anonymous` by
  default, or a trusted proxy header when enabled), and recall is not isolated by
  token. Use this only for loopback/private trusted deployments.

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
`POST /api/memory/capture`, `lore_capture`, and `MemoryProvider.capture(...)`
are the canonical durable capture-to-recall surfaces. `POST /api/capture` is a
supported compatibility endpoint for the page-oriented draft review, status,
promotion, UI, and older SDK workflow.

HTTP:

```bash
curl -sS https://lore.example/api/memory/capture \
  -H "Authorization: Bearer $LORE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Pixl renders all text as garbage on Illustrious XL.",
    "agent_name": "pixie",
    "lane": "project",
    "metadata": {"confidence": "high"},
    "provenance": {
      "task_ids": ["flow_000770"],
      "source_paths": ["ops/deploy/Update-Server.sh"],
      "evidence": "Verified in the deploy script."
    }
  }'
```

In authenticated modes the server ignores `actor` and `agent_name`/`agent` values
for tenancy. It stamps the capture actor and notes namespace agent from the
authenticated request actor. Body values are advisory context only and cannot
write memory as another actor.

MCP tool: `lore_capture`. Python SDK: `MemoryProvider.capture(...)`. The MCP
tool exposes one typed `provenance` object for generic sources, paths, URLs,
evidence, tasks, and related references. It does not expose writable `actor`;
the authenticated actor stamp is authoritative.

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

On authenticated instances with LLM extraction configured, that "just works"
path is still actor-scoped: capture and recall must use the same actor token by
default. A different actor token will see `count: 0` unless an admin explicitly
uses `cross_actor=true`. In other words, successful capture -> recall on those
instances depends on both consolidation and actor scope, not just the presence
of extracted claims.

If you disable auto-consolidation (for very high write volume plus a scheduled
runner), drive it explicitly with `POST /api/consolidation/run` (MCP
`lore_consolidation_run`). That manual run stays safe by default:
`dry_run=true` and `max_auto_apply=0`, so argument-less calls preview work and
do not auto-apply plans. Recall is **self-diagnosing**: a `count: 0` response
includes `pending_captures` and a `hint` telling you whether memory is genuinely
absent or just awaiting consolidation.

On a default install with no LLM key (`LORE_LLM_PROVIDER=none`), extraction is
deterministic: it emits coarse claims keyed off the capture text rather than
fine-grained structured facts. The predicate is inferred heuristically from the
text — one of a small fixed set (`is`, `has`, `uses`, `requires`, `depends_on`,
`supports`, `prevents`, `stores`, `routes`, `runs`), falling back to `describes`
when nothing matches. Recall still works; configure an LLM provider (see
[llm-provider-config.md](llm-provider-config.md)) for richer
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
| **recency** | Exponential freshness from the claim's update time. Half-life 30 days. | `updated_at` |
| **salience** | How often a caller has explicitly acknowledged using the claim, log-scaled and saturating. | `access_count` |
| **relevance** | Lexical overlap between the query and the claim text. Only mixed in when a query is supplied. | query vs. subject/predicate/object |
| **semantic_similarity** | Embedding cosine similarity over the leading candidate pool. Only contributes when a query is supplied *and* a dense store is configured. | query vs. claim embeddings |

The base weights are `strength 0.45, recency 0.25, salience 0.15, relevance 0.05,
semantic_similarity 0.10`, but the effective weights are conditional on the
request. `semantic_similarity` only carries weight when a query is supplied *and*
a dense store is configured; otherwise its `0.10` collapses into `relevance`, so
`relevance` becomes `0.15`. When no query is supplied at all, the relevance weight
is then redistributed across the remaining signals. The weights always sum to 1,
and the exact weights used for a request are returned in the response `weights`
field.

Recalling a claim is read-only by default: repeated `GET /api/memory/recall`
requests do not increment `access_count`, stamp `last_accessed_at`, change recency,
or reset decay age. When a caller intentionally uses returned claims and wants to
boost salience, call `POST /api/memory/recall/ack` with their `candidate_id`
values. The compatibility query flag `record_access=true` still stamps access on
the returned claims, but new clients should prefer the explicit ack endpoint.

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
      "recall_signals": {"total": 0.71, "strength": 0.55, "recency": 0.97, "salience": 0.46, "relevance": 1.0, "semantic_similarity": 0.0}
    }
  ]
}
```

By default, authenticated recall is scoped to the caller's actor even when no
`actor` filter is supplied. A non-admin caller cannot read another actor's claims
by passing `actor`. Admin callers are also scoped by default; to recall across
actors they must explicitly set `cross_actor=true`. With `cross_actor=true`, an
admin may either omit `actor` to query all actors or set `actor` to target one
actor.

REST filters (`GET /api/memory/recall`): `subject`, `lane`, `actor`,
`min_strength`, `valid_at`, `limit`, `record_access`, `cross_actor`.

MCP tool: `lore_recall` — same ranking, but the parameter set differs slightly.
It accepts `offset` for pagination instead of REST's `valid_at` temporal filter;
otherwise it shares `query`, `subject`, `lane`, `actor`, `min_strength`, `limit`,
`record_access`, and `cross_actor`. Python SDK:
`MemoryProvider.recall(query=..., lane=..., actor=...)`.

## Prompt-ready context

`GET /api/memory/context` is the read-only prompt assembly surface. It runs the
same scoped recall query, optionally adds hybrid RAG page hits, and formats the
result as compact markdown with inline `[claim:...]` and `[page:...]` citations.
It does not call an LLM. The returned `token_count` is Lore's deterministic
whitespace-token estimate, and the `context` string is bounded by both
`max_tokens` and `max_chars`.

```bash
curl -sS "https://lore.example/api/memory/context?query=memory+backend&limit=5&max_tokens=900&max_chars=4000" \
  -H "Authorization: Bearer $LORE_API_KEY"
```

Python SDK:
`MemoryProvider.context(query=...)`, `MemoryProvider.to_openai(query=...)`, and
`MemoryProvider.to_anthropic(query=...)`.

```python
from lore_sdk.memory_provider import MemoryProvider

provider = MemoryProvider(base_url="https://lore.example", api_key="...")
context = provider.context("memory backend", max_tokens=900, max_chars=4000)
openai_messages = provider.to_openai("memory backend")
anthropic_blocks = provider.to_anthropic("memory backend")
```

To acknowledge use:

```bash
curl -sS -X POST "https://lore.example/api/memory/recall/ack" \
  -H "Authorization: Bearer $LORE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"candidate_ids":["..."]}'
```

MCP tool: `lore_ack_recall`. Python SDK:
`MemoryProvider.acknowledge_recall(candidate_ids)`.

### Recall vs. search vs. RAG

- **`/api/memory/recall`** — ranked claims from the durable ledger. The default
  "what do I know about X" memory read. Fast, explainable, decay-aware.
- **`/api/search`** — full-text/BM25 over Markdown pages. Document lookup.
- **`/api/rag/retrieve-expanded`** — hybrid retrieval with multi-hop context-graph
  expansion, relevance paths, and supporting/contradicting claims. Use when an agent
  needs assembled context with explanations rather than ranked facts.
- **`/api/memory/context`** - deterministic markdown prompt context assembled
  from recall claims plus optional RAG page hits, with inline citations and
  caller-provided token/character bounds.

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
[`eval/test_hybrid_recall_eval.py`](../eval/test_hybrid_recall_eval.py) adds a
LOCOMO-shaped multi-session no-regression gate over `/api/rag/retrieve` plus
`recall_claims`, with explicit paraphrase, graph-expansion,
contradiction-update, and distractor expectations.

## Surface summary

| Operation | HTTP | MCP tool | Python SDK |
| --- | --- | --- | --- |
| Capture memory | `POST /api/memory/capture` | `lore_capture` | `MemoryProvider.capture` |
| Consolidate (safe preview by default) | `POST /api/consolidation/run` | `lore_consolidation_run` | — |
| Recall ranked claims | `GET /api/memory/recall` | `lore_recall` | `MemoryProvider.recall` |
| Prompt-ready context | `GET /api/memory/context` | - | `MemoryProvider.context` |
| Search pages | `GET /api/search` | `lore_search` | `LoreClient.search` |
| Assembled context | `POST /api/rag/retrieve-expanded` | `lore_rag_context_expanded` | — |
| Memory health | `GET /api/memory/health` | `lore_heartbeat_review` | — |
