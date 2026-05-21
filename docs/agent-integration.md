# Lore Agent Integration Recipes

Lore gives agents a shared Markdown-backed project memory through REST, SDKs,
and MCP. Agents should read before planning, write observations as captures, and
promote durable knowledge once evidence is sufficient.

## Codex CLI

Setup:
1. Install the Python SDK from `sdk/python`.
2. Configure `LORE_BASE_URL` and, if required, `LORE_API_KEY`.
3. Prefer `/mcp` for tool-aware clients and the Python SDK for scripts.

Recommended workflow:
- Read `/api/search`, `/api/pages/{page_id}`, and `/api/rag/retrieve` before
  changing code.
- Write uncertain findings to `/api/capture`.
- Promote or incorporate captures autonomously when evidence and target pages
  are clear; escalate low-confidence or conflicting captures for manual audit.

```python
from lore_sdk import LoreClient

client = LoreClient(base_url="https://lore.example.com", auth_token="token")
hits = client.search("gpu runtime deployment")
page = client.get_page(hits["hits"][0]["page"]["id"])
client.capture(
    observation="Deploy script expects Caddy before Lore restart.",
    sources=["ops/deploy/Update-Server.sh"],
    confidence="medium",
)
```

## OpenAI API Agents

Setup:
1. Add Lore REST or SDK calls as agent tools.
2. Store the Lore base URL and auth token in your agent runtime secrets.
3. Set tool descriptions to distinguish canonical reads from draft captures.

Recommended workflow:
- Retrieve context with `/api/rag/retrieve` at task start.
- Fetch canonical pages before making project claims.
- Capture build, deploy, and debugging observations with source paths.

```bash
curl -sS https://lore.example.com/api/rag/retrieve \
  -H "Authorization: Bearer $LORE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"Lore backup restore process","limit":5}'
```

## Claude/MCP Clients

Setup:
1. Register the Lore MCP endpoint at `https://lore.example.com/mcp`.
2. Use streamable HTTP transport.
3. Include a Lore-owned bearer key generated through `/api/api-keys`.

Recommended workflow:
- Use `resources/list` and `resources/read` for known pages.
- Use `tools/list` to discover search, graph, capture, and RAG tools.
- Capture only evidence-backed observations, including source file paths.

```bash
curl -sS https://lore.example.com/mcp \
  -H "Authorization: Bearer $LORE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Cursor-Like Tools

Setup:
1. Add a project memory provider that calls Lore REST APIs.
2. Map file paths to `/api/code-references/{code_path}`.
3. Cache short-lived search results per workspace session.

Recommended workflow:
- Query `/api/code-references/{path}` when opening a file.
- Search Lore with branch, service, and feature names.
- Show source citations next to retrieved context.

```bash
curl -sS "https://lore.example.com/api/code-references/services/lore/lore_app/main.py" \
  -H "Authorization: Bearer $LORE_API_KEY"
```

## CI Bots

Setup:
1. Give the CI runtime a token that can write captures.
2. Add a post-build step that reports noteworthy failures or deploy outcomes.
3. Include commit SHA, workflow URL, and relevant log excerpts.

Recommended workflow:
- Capture failed deploys, flaky tests, migration outcomes, and recovery steps.
- Use `confidence=high` only for direct build or deploy facts.
- Link captures to service or runbook pages through `related_pages`.

```bash
curl -sS https://lore.example.com/api/capture \
  -H "Authorization: Bearer $LORE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Lore CI failed on search tests",
    "observation": "pytest failed in services/lore/tests/test_search_index.py on the main branch.",
    "source_task": "github-actions/lore-ci",
    "related_pages": ["services/lore"],
    "confidence": "high",
    "sources": ["$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"]
  }'
```

## Custom Agents

Setup:
1. Choose REST, the TypeScript SDK, or the Python SDK.
2. Configure a read-before-write policy in the agent prompt.
3. Route all uncertain new knowledge through captures.

Recommended workflow:
- Start with catalog and search to find the right namespace.
- Read canonical pages fully before editing or answering.
- Use captures for uncertain observations; use page upsert for confirmed
  canonical docs.

```python
from lore_sdk import LoreClient

client = LoreClient(base_url="https://lore.example.com", auth_token="token")
catalog = client.catalog()
context = client.rag_retrieve("how to deploy the operations hub")
client.upsert_page(
    "runbooks/deploy-note",
    "---\ntitle: Deploy Note\nkind: runbook\nvisibility: internal\n---\n\n# Deploy Note\n\nReviewed procedure notes.\n",
)
```
