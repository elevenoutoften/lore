# Lore

Lore is an **agent memory backend**: a fast, shared, Markdown-backed memory
store that agents (Hermes, OpenClaw, Codex, and others) connect to with a token
to read, write, link, and recall project knowledge over HTTP and MCP.

Design principles:

- **Agent-first.** The HTTP API and MCP endpoint are the primary product — any
  agent with a bearer token reads and maintains memory. Multi-hop retrieval, a
  context graph, reasoning traces, provenance, and autonomous consolidation
  assist recall.
- **Markdown is the source of truth.** Memory is durable, inspectable, and
  mergeable Markdown, not an opaque blob store.
- **The browser surface is intentionally minimal.** A readable wiki view, a
  graph, and a couple-of-clicks settings/keys page — not somewhere agents (or
  most humans) need to go.

The [Agent Memory Contract](docs/agent-memory-contract.md) is the canonical
description of the product surface: how an agent connects with a token, captures
durable memory, and recalls it ranked by relevance, recency, and salience over
HTTP and MCP.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

### Running tests

```bash
pytest
```

### Lint and format checks

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
```

### Coverage

```bash
pytest --cov=lore_app --cov-report=term-missing --cov-fail-under=50
```

### Dependency audit

```bash
pip install -e ".[dev]" pip-audit
pip-audit --local --desc
```

### Starting the server

```bash
LORE_CONTENT_DIR=./sample-vault uvicorn lore_app.asgi:app --reload --port 8078
```

The public deployment runs behind a reverse proxy (e.g. Caddy). Browser
requests use bearer key authentication. Agents use Lore-owned bearer keys created
through the `/api-keys` browser page or `/api/api-keys`; Lore no longer depends
on Flow API keys.

**Bootstrapping the first key.** On a default local install (`LORE_AUTH_MODE=none`,
loopback) the `/api-keys` and `/settings` pages are open to the local operator —
just create a key. With `LORE_AUTH_MODE=api_key`, mint the first admin key without
a running server using the CLI below, then paste it into the `/api-keys` page's
bearer-token field. With `LORE_AUTH_MODE=bearer`/`basic` the keys/settings pages
are managed by the holder of `LORE_AUTH_SECRET` — send that secret as the bearer
token:

```bash
lore key create --name bootstrap --role admin
```

## Configuration

`LORE_AUTH_MODE` accepts only `none`, `bearer`, `basic`, or `api_key`. Any other
value causes Lore to fail to start rather than silently running without auth.

`bearer` and `basic` require `LORE_AUTH_SECRET` to be set to a non-empty value.
`api_key` uses the API key database configured by `LORE_API_KEYS_DB`.
`none` disables auth entirely and is only suitable for private, trusted
networks.

## API

Core and configuration:

- `GET /healthz`
- `GET /healthz/config`
- `GET /api/version`
- `GET /api/config`
- `GET /api/audit`
- `GET /api/semantics`
- `GET /api/catalog`
- `GET /api/frontmatter/spec`

Pages, decisions, procedures, and code references:

- `GET /api/pages`
- `GET /api/pages/{page_id}`
- `PUT /api/pages/{page_id}`
- `DELETE /api/pages/{page_id}`
- `GET /api/pages/{page_id}/rendered`
- `GET /api/pages/{page_id}/history`
- `GET /api/pages/{page_id}/links`
- `POST /api/pages/{page_id}/stub`
- `GET /api/decisions`
- `GET /api/decisions/template`
- `GET /api/procedures`
- `GET /api/procedures/template`
- `GET /api/procedures/candidates`
- `POST /api/procedures/candidates`
- `POST /api/procedures/export`
- `GET /api/procedures/{page_id}/export`
- `GET /api/procedures/{page_id}`
- `POST /api/procedures/{page_id}/validate`
- `GET /api/code-references/{code_path}`
- `POST /api/code-ingest/{service_id}`
- `GET /api/code-ingest/{service_id}/inventory`

Search, graph, RAG, and lint:

- `GET /api/search`
- `POST /api/search/reindex`
- `GET /api/search/fts`
- `GET /api/search/bm25`
- `GET /api/links`
- `GET /api/graph/stats`
- `GET /api/graph/enriched`
- `GET /api/graph/sources`
- `GET /api/graph/analytics`
- `GET /api/context-graph`
- `POST /api/context-graph/neighbors`
- `POST /api/context-graph/paths`
- `POST /api/context-graph/explain`
- `POST /api/rag/retrieve`
- `POST /api/rag/retrieve-expanded`
- `POST /api/rag/evaluate`
- `GET /api/lint`
- `GET /api/lint/fixable`
- `GET /api/lint/stale`
- `GET /api/lint/contradictions`

Captures, heartbeat, distillation, extraction, and memory:

- `POST /api/capture`
- `GET /api/captures`
- `GET /api/captures/digest`
- `POST /api/captures/{page_id}/status`
- `POST /api/captures/{page_id}/promote`
- `GET /api/promotions`
- `GET /api/heartbeat`
- `POST /api/heartbeat/captures`
- `POST /api/distill/daily`
- `GET /api/distill/daily/{target_date}`
- `POST /api/distill/promote/{target_date}`
- `GET /api/distill/pending`
- `POST /api/extraction/run`
- `GET /api/extraction/status`
- `POST /api/extraction/reset`
- `GET /api/extraction/batches`
- `GET /api/extraction/candidates`
- `POST /api/memory/capture`
- `GET /api/memory/recall`
- `GET /api/memory/health`

Ledger, consolidation, provenance, traces, policies, and precedents:

- `POST /api/ledger/reinforce`
- `POST /api/ledger/supersede`
- `POST /api/ledger/activate/{candidate_id}`
- `POST /api/ledger/reject/{candidate_id}`
- `POST /api/ledger/archive/{candidate_id}`
- `POST /api/ledger/decay`
- `GET /api/ledger/claims`
- `GET /api/ledger/candidates`
- `GET /api/consolidation/status`
- `POST /api/consolidation/run`
- `POST /api/consolidation/rollback/{plan_id}`
- `POST /api/consolidation/plan`
- `GET /api/consolidation/plans`
- `GET /api/consolidation/plans/{plan_id}`
- `POST /api/consolidation/apply/{plan_id}`
- `POST /api/consolidation/reject/{plan_id}`
- `GET /api/consolidation/blocked`
- `GET /api/provenance/{entity_type}/{entity_id}`
- `POST /api/traces`
- `GET /api/traces`
- `GET /api/traces/{trace_id}`
- `PATCH /api/traces/{trace_id}`
- `GET /api/policies`
- `POST /api/policies`
- `GET /api/policies/{policy_id}`
- `DELETE /api/policies/{policy_id}`
- `POST /api/precedents`

API key management:

- `GET /api/api-keys`
- `POST /api/api-keys`
- `POST /api/api-keys/{api_key_id}/revoke`

Runtime settings (secrets are never returned; only `configured` + masked hint):

- `GET /api/settings/llm` (any valid Lore API key)
- `PUT /api/settings/llm` (admin Lore API key)
- `DELETE /api/settings/llm` (admin Lore API key)

Page IDs are slash-separated Markdown paths without the `.md` suffix, for
example `projects/example-project` or `services/lore`.

The raw page endpoint returns Markdown for agents and sync tools. The rendered
endpoint returns sanitized HTML, a table of contents, and resolved link metadata
for browser clients.

The link endpoints return graph-aware context for agents and the browser reader:
outgoing links, backlinks, and broken internal links.

The lint endpoint returns knowledge-quality warnings for agents and maintainers:
broken internal links, missing metadata or sources, stale pages, duplicate
titles, orphan pages, low-confidence pages, and contradiction markers. Lint is
advisory; raw Markdown remains the source of truth.

The capture endpoint writes rough agent memory into ordinary draft Markdown
pages for autonomous consolidation. Shared captures go under
`inbox/YYYY-MM-DD/<slug>`; agent-scoped notes go under
`notes/<agent>/YYYY-MM-DD/<slug>`. Captures can include a source task, related
pages, confidence, suggested target page, and sources. Captured memory is not
accepted project truth until an agent, automation, or explicit operator action
promotes or incorporates it into canonical Lore pages.

The captures endpoint lists the intake queue. It defaults to draft captures and
accepts `status=all` to show every capture status. Human review is an escalation
path for low-confidence, conflicting, or sensitive captures, not the default
memory-management loop.

## Governance

Lore tracks both confidence and epistemic provenance. See
[docs/governance.md](docs/governance.md) for the epistemic status labels
(`operator_declared`, `retrieved`, `inferred`, `assumption`, `hearsay`) and review
guidance.
## Wiki links

Lore stores ordinary Markdown, but the browser reader understands Lore page
links:

```md
[Pixl](services/pixl)
[Pixl](services/pixl.md)
[Workflow Engine](../services/workflow-engine)
[[services/pixl]]
[[Pixl|services/pixl]]
```

Internal links are rewritten to the page route, for example
`/services/pixl`. Missing internal pages render with a warning style so they are
easy to repair.

## Roadmap

See [docs/roadmap.md](docs/roadmap.md) for planned phases: memory foundation,
security hardening, and beta release.

## MCP

`POST /mcp` accepts JSON-RPC requests for:

- `initialize`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`
- `resources/templates/list`
- `prompts/list`

Tools:

- `lore_list_pages`
- `lore_read_page`
- `lore_search`
- `lore_list_lanes`
- `lore_list_actors`
- `lore_rag_context`
- `lore_rag_context_expanded`
- `lore_recall`
- `lore_link_graph`
- `lore_context_graph`
- `lore_graph_analytics`
- `lore_context_graph_neighbors`
- `lore_context_graph_paths`
- `lore_explain_context`
- `lore_page_links`
- `lore_lint`
- `lore_stale_pages`
- `lore_contradiction_review`
- `lore_frontmatter_spec`
- `lore_list_procedures`
- `lore_create_procedure`
- `lore_export_procedure`
- `lore_capture`
- `lore_list_captures`
- `lore_capture_digest`
- `lore_transition_capture`
- `lore_promote_capture`
- `lore_promotion_audit`
- `lore_create_stub`
- `lore_update_metadata`
- `lore_ingest_service`
- `lore_create_decision`
- `lore_create_trace`
- `lore_get_trace`
- `lore_get_provenance`
- `lore_list_traces`
- `lore_list_policies`
- `lore_find_precedents`
- `lore_get_policy`
- `lore_upsert_page`
- `lore_distill_daily`
- `lore_get_daily`
- `lore_promote_daily`
- `lore_heartbeat_review`
- `lore_heartbeat_summary`
- `lore_heartbeat_audit`
- `lore_find_repeated_captures`
- `lore_propose_procedure_candidate`
- `lore_consolidation_status`
- `lore_consolidation_run`
- `lore_consolidation_rollback`
- `lore_list_patch_plans`
- `lore_preview_patch`
- `lore_apply_patch`
- `lore_reject_patch`
- `lore_review_batch`

## Demo Vault

Lore includes a self-contained sample vault at `sample-vault/`. It contains
valid Lore frontmatter, service pages, architecture notes, guides, a decision,
a runbook, and wikilinks that demonstrate cross-page navigation.

Initialize a local content directory from the sample data:

```bash
scripts/init-demo-vault.sh /tmp/lore-demo-pages
LORE_CONTENT_DIR=/tmp/lore-demo-pages uvicorn lore_app.asgi:app --reload --port 8078
```

From the repo root, use:

```bash
./scripts/init-demo-vault.sh ./data/pages
```
