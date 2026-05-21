# Lore

Lore is the Lore LLM wiki: a Markdown-backed project registry for humans,
coding agents, and digest agents.

It is intentionally small in v1:

- Markdown files are the source of truth.
- The HTTP API lists, reads, searches, and upserts pages.
- The MCP endpoint exposes the same wiki through tools and resources.
- The browser reader renders Markdown with headings, tables, code blocks, a
  table of contents, and Lore-aware internal links.
- RAG is a later phase built on top of Markdown, context graph, and provenance
  conventions, not a replacement for canonical pages.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
pytest
LORE_CONTENT_DIR=../../lore uvicorn lore_app.main:app --reload --port 8078
```

The public deployment runs behind a reverse proxy (e.g. Caddy). Browser
requests use bearer key authentication. Agents use Lore-owned bearer keys created
through the `/api-keys` browser page or `/api/api-keys`; Lore no longer depends
on Flow API keys.

## API

- `GET /healthz`
- `GET /api/api-keys`
- `POST /api/api-keys`
- `POST /api/api-keys/{api_key_id}/revoke`
- `POST /api/capture`
- `GET /api/captures`
- `GET /api/links`
- `GET /api/lint`
- `GET /api/pages`
- `GET /api/pages/{page_id}`
- `GET /api/pages/{page_id}/links`
- `GET /api/pages/{page_id}/rendered`
- `PUT /api/pages/{page_id}`
- `DELETE /api/pages/{page_id}`
- `GET /api/search?q=...`

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

Tools:

- `lore_capture`
- `lore_list_captures`
- `lore_list_pages`
- `lore_read_page`
- `lore_search`
- `lore_link_graph`
- `lore_page_links`
- `lore_lint`
- `lore_upsert_page`

## Demo Vault

Lore includes a self-contained sample vault at `sample-vault/`. It contains
valid Lore frontmatter, service pages, architecture notes, guides, a decision,
a runbook, and wikilinks that demonstrate cross-page navigation.

Initialize a local content directory from the sample data:

```bash
scripts/init-demo-vault.sh /tmp/lore-demo-pages
LORE_CONTENT_DIR=/tmp/lore-demo-pages uvicorn lore_app.main:app --reload --port 8078
```

From the repo root, use:

```bash
./scripts/init-demo-vault.sh ./data/pages
```
