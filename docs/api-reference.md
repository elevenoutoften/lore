# API Reference

Base URL for local examples:

```bash
export LORE_URL=http://localhost:8000
```

When auth is enabled, add `-H "Authorization: Bearer $LORE_TOKEN"` or use HTTP
basic auth.

A fixed set of paths is always public and exempt from auth in every mode:
`/healthz`, `/healthz/config`, `/api/login`, `/api/logout`, and `/static`
(plus everything under `/static/`). `/metrics` follows the configured auth mode
by default; set `LORE_METRICS_PUBLIC=true` to intentionally expose it without
credentials for a protected scraper. `/healthz`, `/metrics`, and
`/api/memory/health` expose `extraction_tokens_total`,
`extraction_tokens_last_batch`, and `extraction_tokens_recent_average`, where
the recent average is computed across the latest extraction batches with
recorded token usage.

## Core

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Health and metrics. |
| `GET` | `/api/version` | Package, Python, and API versions. |
| `GET` | `/api/config` | Effective configuration. Admin only. |
| `GET` | `/api/catalog` | Known kinds, visibilities, and tags. |
| `GET` | `/api/frontmatter/spec` | Frontmatter field spec. |
| `GET` | `/api/semantics` | Confidence, status, and visibility definitions. |
| `GET` | `/api/audit` | Audit log query. Admin only. |

```bash
curl -sS "$LORE_URL/healthz"
curl -sS "$LORE_URL/api/version"
curl -sS "$LORE_URL/api/config"
curl -sS "$LORE_URL/api/catalog"
curl -sS "$LORE_URL/api/frontmatter/spec"
curl -sS "$LORE_URL/api/semantics"
curl -sS "$LORE_URL/api/audit?page_id=services/lore&limit=20"
```

## Pages

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/pages` | List pages. Query: `kind`, `visibility`, `q`, `limit`. |
| `GET` | `/api/pages/{page_id}` | Read raw page detail. |
| `PUT` | `/api/pages/{page_id}` | Create or replace a page. |
| `DELETE` | `/api/pages/{page_id}` | Delete a page. |
| `GET` | `/api/pages/{page_id}/rendered` | Read sanitized HTML, TOC, and links. |
| `GET` | `/api/pages/{page_id}/links` | Outgoing links, backlinks, and missing links. |
| `GET` | `/api/pages/{page_id}/history` | Audit entries for a page. |
| `PATCH` | `/api/pages/{page_id}/metadata` | Update selected frontmatter fields. |
| `POST` | `/api/pages/{page_id}/stub` | Create a stub page if it does not exist. |

```bash
curl -sS "$LORE_URL/api/pages?kind=service&limit=20"
curl -sS "$LORE_URL/api/pages/services/lore"
curl -sS "$LORE_URL/api/pages/services/lore/rendered"
curl -sS "$LORE_URL/api/pages/services/lore/links"
curl -sS "$LORE_URL/api/pages/services/lore/history"
```

Create or update:

```bash
curl -sS -X PUT "$LORE_URL/api/pages/services/demo" \
  -H "Content-Type: application/json" \
  -d '{"content":"---\ntitle: Demo Service\nkind: service\nvisibility: internal\nsummary: API-created page.\n---\n\n# Demo Service\n\nCreated through the REST API.\n"}'
```

Patch metadata:

```bash
curl -sS -X PATCH "$LORE_URL/api/pages/services/demo/metadata" \
  -H "Content-Type: application/json" \
  -d '{"status":"active","confidence":"high","owner":"docs"}'
```

Create a stub:

```bash
curl -sS -X POST "$LORE_URL/api/pages/references/missing-page/stub" \
  -H "Content-Type: application/json" \
  -d '{"title":"Missing Page","kind":"page","source_page":"services/demo"}'
```

Delete:

```bash
curl -sS -X DELETE "$LORE_URL/api/pages/services/demo"
```

## Search and RAG

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/search` | Repository search. Query: `q`, `kind`, `visibility`, `limit`. |
| `POST` | `/api/search/reindex` | Rebuild search and vector indexes. |
| `GET` | `/api/search/fts` | Full-text search index. |
| `GET` | `/api/search/bm25` | BM25 search index. |
| `POST` | `/api/rag/retrieve` | Hybrid retrieval over search, vectors, and graph. |
| `POST` | `/api/rag/retrieve-expanded` | Hybrid retrieval with bounded context graph expansion, path explanations, and related claim/decision/trace IDs. |
| `POST` | `/api/rag/evaluate` | Evaluate retrieval queries. |

```bash
curl -sS "$LORE_URL/api/search?q=deploy&limit=5"
curl -sS "$LORE_URL/api/search/fts?q=deploy"
curl -sS "$LORE_URL/api/search/bm25?q=deploy"
curl -sS -X POST "$LORE_URL/api/search/reindex"
```

```bash
curl -sS -X POST "$LORE_URL/api/rag/retrieve" \
  -H "Content-Type: application/json" \
  -d '{"query":"how to deploy Lore","limit":5}'
```

```bash
curl -sS -X POST "$LORE_URL/api/rag/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"k":5,"queries":[{"query":"Lore deployment","expected_ids":["services/lore"]}]}'
```

## Graph and Lint

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/links` | Whole-vault link graph. |
| `GET` | `/api/graph/stats` | Page, link, and broken-link counts. |
| `GET` | `/api/graph/enriched` | Graph with node counts and metadata. |
| `GET` | `/api/graph/sources` | Source reference edges. |
| `GET` | `/api/graph/analytics` | Centrality and community analytics over the context graph. |
| `GET` | `/api/context-graph` | Multi-hop context graph over pages, captures, claims, traces, etc. |
| `POST` | `/api/context-graph/neighbors` | Neighbors of the given nodes. |
| `POST` | `/api/context-graph/paths` | Paths between nodes. |
| `POST` | `/api/context-graph/explain` | Explain how nodes are connected. |
| `GET` | `/api/lint` | Full Lore lint report. |
| `GET` | `/api/lint/fixable` | Auto-fixable lint issues. |
| `GET` | `/api/lint/stale` | Stale page queue. |
| `GET` | `/api/lint/contradictions` | Contradiction marker review. |

```bash
curl -sS "$LORE_URL/api/links"
curl -sS "$LORE_URL/api/graph/stats"
curl -sS "$LORE_URL/api/graph/enriched"
curl -sS "$LORE_URL/api/graph/sources"
curl -sS "$LORE_URL/api/lint"
curl -sS "$LORE_URL/api/lint/fixable"
curl -sS "$LORE_URL/api/lint/stale"
curl -sS "$LORE_URL/api/lint/contradictions"
```

## Capture Queue and Promotion

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/capture` | Create a compatibility-rich draft page for review/status/promotion workflows. |
| `GET` | `/api/captures` | List captures. Query: `status`, `limit`. |
| `GET` | `/api/captures/digest` | Group draft/review captures. |
| `POST` | `/api/captures/{page_id}/status` | Change capture status. |
| `POST` | `/api/captures/{page_id}/promote` | Promote capture into a target page. |
| `GET` | `/api/promotions` | Promotion audit. |

`POST /api/capture` is the compatibility-rich, page-oriented draft inbox/review
write. Its page response supports listing, status transitions, promotion, the UI,
and older SDK clients. It remains supported and runs shared post-capture side
effects. New agent clients should instead use `POST /api/memory/capture`,
`lore_capture`, or `MemoryProvider.capture(...)` for the canonical durable
capture-to-recall loop.

```bash
curl -sS "$LORE_URL/api/captures?status=draft&limit=50"
curl -sS "$LORE_URL/api/captures/digest"
curl -sS -X POST "$LORE_URL/api/captures/inbox/2026-05-04/deploy-finding/status" \
  -H "Content-Type: application/json" \
  -d '{"status":"review"}'
curl -sS -X POST "$LORE_URL/api/captures/inbox/2026-05-04/deploy-finding/promote" \
  -H "Content-Type: application/json" \
  -d '{"target_page_id":"runbooks/deploy-lore"}'
curl -sS "$LORE_URL/api/promotions"
```

## Heartbeat, Distillation, Extraction, and Memory

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/heartbeat` | Self-audit review (stale, contradictions, low-confidence, expired, procedure issues). |
| `POST` | `/api/heartbeat/captures` | Create captures from heartbeat findings. |
| `POST` | `/api/distill/daily` | Distill a day's captures into a daily note. Body: `{date?, actor?}`. |
| `GET` | `/api/distill/daily/{target_date}` | List the captures for a day. |
| `POST` | `/api/distill/promote/{target_date}` | Promote a daily note. |
| `GET` | `/api/distill/pending` | Days with captures not yet distilled. |
| `POST` | `/api/extraction/run` | Run LLM/deterministic extraction. Body: `{provider?, capture_ids?, dry_run, batch_size}`. |
| `GET` | `/api/extraction/status` | Extraction batch status. |
| `POST` | `/api/extraction/reset` | Reset extraction state. |
| `POST` | `/api/extraction/deadletters/{deadletter_id}/retry` | Retry one extraction dead-letter and resolve it when candidates are produced. |
| `GET` | `/api/extraction/batches` | List extraction batches. |
| `GET` | `/api/extraction/candidates` | List extracted candidates. Query: `status`, `type`, `actor`, `cross_actor`, `limit`. |
| `GET` | `/api/ledger/candidates` | List ledger candidates with provenance. Query: `status`, `type`, `capture_id`, `page_id`, `lane`, `actor`, `cross_actor`, `limit`. |
| `POST` | `/api/ledger/cleanup/disposable-candidates` | Admin-only retention cleanup for heartbeat/eval/test/smoke/probe candidates; rejects disposable-only candidates, archives active rows, scrubs mixed provenance, and invalidates the context graph cache. |
| `POST` | `/api/memory/capture` | Lightweight memory capture. Authenticated modes server-stamp actor/agent from the token actor. |
| `GET` | `/api/memory/recall` | Ranked claim recall. Authenticated modes are scoped to the token actor; admins must set `cross_actor=true` for cross-actor reads. Read-only by default; `record_access=false` unless explicitly set. |
| `GET` | `/api/memory/context` | Deterministic prompt-ready markdown context assembled from recall claims and optional RAG page hits. Query: `query`, `limit`, `max_tokens`, `max_chars`, `include_recall`, `include_rag`, `rag_expand_hops`, `cross_actor`. |
| `POST` | `/api/memory/recall/ack` | Acknowledge used recall claims. Body: `{candidate_ids}`. |
| `GET` | `/api/memory/health` | Memory subsystem health counts plus extraction-token totals, last-batch tokens, and the recent batch average. |

```bash
curl -sS -X POST "$LORE_URL/api/memory/capture" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The deploy runbook expects the search index to be rebuilt after restore.",
    "agent_name": "api-reference",
    "namespace": "notes",
    "lane": "ops",
    "task_id": "api-reference",
    "metadata": {
      "title": "Deploy finding",
      "capture_date": "2026-05-04",
      "related_pages": ["runbooks/deploy-lore"],
      "confidence": "medium",
      "suggested_target_page": "runbooks/deploy-lore"
    },
    "provenance": {
      "sources": ["docs/api-reference.md"],
      "source_paths": ["lore_app/routes/memory.py"],
      "evidence": "Verified against the current memory route."
    }
  }'
```

```json
{
  "capture_id": "notes/api-reference/2026-05-04/deploy-finding",
  "timestamp": "2026-05-04T12:20:00+00:00"
}
```

## Runtime Settings

Secrets are never returned: responses expose `*_configured` (bool) and a masked
`*_hint` only. See [llm-provider-config.md](llm-provider-config.md).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/settings/llm` | Read the effective LLM provider config (env + stored overrides). Any Lore key. |
| `PUT` | `/api/settings/llm` | Update LLM settings and hot-reload the client. Admin key only. |
| `DELETE` | `/api/settings/llm` | Clear stored overrides; revert to env defaults. Admin key only. |

```bash
curl -sS "$LORE_URL/api/settings/llm" -H "Authorization: Bearer $LORE_TOKEN"
curl -sS -X PUT "$LORE_URL/api/settings/llm" \
  -H "Authorization: Bearer $LORE_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"provider":"ollama","model":"glm-5.1","embedding_model":"embeddinggemma","base_url":"https://ollama.com/v1","api_key":"sk-..."}'
```

`embedding_model` is optional. When it and the primary API key are configured,
Lore hot-rebuilds its sqlite-vec dense index and uses semantic similarity in RAG
and memory recall. Omitting either value preserves the keyless TF-IDF fallback.

## Decisions and Procedures

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/decisions` | List `kind: decision` pages. |
| `GET` | `/api/decisions/template` | Decision page template. |
| `GET` | `/api/procedures` | List `kind: procedure` pages. |
| `GET` | `/api/procedures/template` | Procedure page template. |
| `GET` | `/api/procedures/{page_id}` | Read a procedure page. |
| `GET` | `/api/procedures/{page_id}/export` | Export a procedure as an agent skill. |

```bash
curl -sS "$LORE_URL/api/decisions"
curl -sS "$LORE_URL/api/decisions/template"
curl -sS "$LORE_URL/api/procedures"
curl -sS "$LORE_URL/api/procedures/template"
curl -sS "$LORE_URL/api/procedures/runbooks/deploy-lore"
curl -sS "$LORE_URL/api/procedures/runbooks/deploy-lore/export"
```

## Code Ingest

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/code-references/{code_path}` | Find pages referencing a code path. |
| `POST` | `/api/code-ingest/{service_id}` | Ingest service code. Query: `source_dir`. Admin only. |
| `GET` | `/api/code-ingest/{service_id}/inventory` | Read latest service inventory. Admin only. |

```bash
curl -sS "$LORE_URL/api/code-references/lore_app/main.py"
curl -sS -X POST "$LORE_URL/api/code-ingest/services/lore?source_dir=services/lore"
curl -sS "$LORE_URL/api/code-ingest/services/lore/inventory"
```

## MCP

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/mcp` | MCP endpoint information. |
| `POST` | `/mcp` | Streamable HTTP JSON-RPC endpoint. |

```bash
curl -sS "$LORE_URL/mcp"
curl -sS -X POST "$LORE_URL/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Browser Routes

These return HTML:

- `GET /`
- `GET /search?q=...`
- `GET /embed?mode=page&pageId=...`
- `GET /lint`
- `GET /captures`
- `GET /graph`
- `GET /rag?q=...`
- `GET /procedures`
- `GET /heartbeat`
- `GET /api-keys`
- `GET /settings`
- `GET /pages/{page_id}`
- `GET /{page_id}`

## Request Schemas

`PageUpsert`:

```json
{
  "content": "---\ntitle: Example\nkind: page\nvisibility: internal\n---\n\n# Example\n"
}
```

`MetadataUpdate`:

```json
{
  "owner": "team-name",
  "reviewed_at": "2026-05-04",
  "stale_after": "2026-08-04",
  "confidence": "high",
  "status": "active"
}
```

`StubRequest`:

```json
{
  "title": "Missing Page",
  "kind": "page",
  "source_page": "services/lore"
}
```

`MemoryCaptureRequest`:

```json
{
  "text": "Required.",
  "agent_name": "codex",
  "namespace": "notes",
  "tags": ["agent-memory"],
  "lane": "project",
  "task_id": "flow_000174",
  "trace_id": "trace_123",
  "tool_calls": [{"tool": "search", "query": "lore config"}],
  "constraints": ["docs-only"],
  "policies_applied": ["policy.docs.v1"],
  "provenance": {
    "sources": ["README.md"],
    "source_paths": ["lore_app/main.py"],
    "source_urls": ["https://example.com"],
    "evidence": "Supporting detail."
  },
  "metadata": {
    "title": "Optional title",
    "capture_date": "2026-05-04",
    "related_pages": ["services/lore"],
    "confidence": "medium",
    "suggested_target_page": "services/lore"
  }
}
```

`CaptureStatusUpdate`:

```json
{ "status": "review" }
```

Allowed values: `draft`, `review`, `accepted`, `rejected`, `archived`.

`CapturePromotion`:

```json
{
  "target_page_id": "services/lore",
  "content": "Optional replacement Markdown content."
}
```

RAG retrieve:

```json
{ "query": "deployment runbook", "limit": 10 }
```

RAG expanded retrieve:

```json
{ "query": "routing policy", "limit": 10, "expand_hops": 2, "include_claims": true }
```

Graph and semantic retrieval help discover relevant context, but canonical
Markdown pages remain the source of truth.

RAG evaluate:

```json
{
  "k": 5,
  "queries": [
    { "query": "deployment runbook", "expected_ids": ["runbooks/deploy-lore"] }
  ]
}
```

## Response Schemas

`PageSummary`:

```json
{
  "id": "services/lore",
  "title": "Lore",
  "kind": "service",
  "visibility": "internal",
  "status": "active",
  "summary": "Markdown-backed wiki.",
  "tags": ["wiki"],
  "sources": ["README.md"],
  "updated_at": "2026-05-04T00:00:00+00:00",
  "size": 512
}
```

`PageDetail` adds `content`, `body`, and `frontmatter`.

`SearchResponse`:

```json
{
  "query": "lore",
  "hits": [
    {
      "page": { "id": "services/lore", "title": "Lore" },
      "score": 20,
      "matches": ["Lore stores Markdown pages."]
    }
  ]
}
```

`LinkGraphResponse` includes `pages`, `links`, and `broken_links`.
`PageLinks` includes `page`, `outgoing`, `backlinks`, and `missing_links`.
`LoreLintResponse` includes `checked_pages`, `issue_count`,
`suppressed_count`, and `issues`.

## Error Codes

| Status | Meaning |
| --- | --- |
| `400` | Invalid JSON for MCP. |
| `401` | Missing or invalid auth credentials. |
| `404` | Page, procedure, inventory, or route not found. |
| `409` | Stub creation requested for an existing page. |
| `422` | Validation error, invalid page ID, invalid content, or bad payload. |
| `429` | Write rate limit exceeded. |

FastAPI validation errors use the standard `{"detail":[...]}` shape. Lore
application errors usually use `{"detail":"message"}`.

## Rate Limiting

Write operations are limited to 300 requests per 60 seconds per client key.
Authenticated agents get per-actor write budgets. The client key is resolved by
precedence: the resolved request actor, then the `X-Lore-Agent`/`X-Lore-Actor`
header, then a hashed bearer token, then the first `X-Forwarded-For` address
(only when `LORE_TRUSTED_HEADERS=true`), then the direct client host.
