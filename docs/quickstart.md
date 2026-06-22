# Quickstart

This guide starts a local Lore server, writes the first page, searches it, and
captures draft agent memory.

## Install

Install from source (the distribution is not published to PyPI yet):

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

Start a local server against the bundled demo vault:

```bash
LORE_CONTENT_DIR=./sample-vault uvicorn lore_app.asgi:app --reload --port 8078
```

By default Lore binds to `127.0.0.1` with auth disabled — the safe, zero-config
local setup. To expose it on a network, enable auth (see
[configuration.md](configuration.md)).

Run with Docker (production-style). The image defaults to `api_key` auth, so
persist a db volume, name the container, and mint an admin key to send as a
bearer token:

```bash
docker build -t lore-app .
docker run -d --name lore -p 8078:8000 \
  -v "$PWD/sample-vault:/data/pages" \
  -v "$PWD/lore-db:/data/db" \
  lore-app
docker exec lore lore-admin key create --name quickstart --role admin
```

Copy the printed token; the Docker instance needs it on every API call as
`-H "Authorization: Bearer <token>"`. (The loopback `uvicorn` flow above runs
`LORE_AUTH_MODE=none` and needs no token — pick whichever server you started.)

Set a base URL for examples:

```bash
export LORE_URL=http://localhost:8078
```

## First Page

Page IDs are slash-separated paths without `.md`.

```bash
curl -sS -X PUT "$LORE_URL/api/pages/projects/demo" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "---\ntitle: Demo Project\nkind: project\nvisibility: internal\nsummary: First Lore page.\n---\n\n# Demo Project\n\nLore stores Markdown as source-of-truth content.\n"
  }'
```

Read it back:

```bash
curl -sS "$LORE_URL/api/pages/projects/demo"
```

## Search

```bash
curl -sS "$LORE_URL/api/search?q=demo&limit=5"
```

Full-text and BM25-backed search endpoints are also available:

```bash
curl -sS "$LORE_URL/api/search/fts?q=Markdown"
curl -sS "$LORE_URL/api/search/bm25?q=Markdown"
```

## Capture

The canonical durable agent memory write path is `POST /api/memory/capture`. Use typed
top-level routing fields, `provenance` for sources/evidence, and `metadata` only
for extra capture-page fields such as title and confidence. `POST /api/capture`
is the retained draft inbox/review contract for listing, status, promotion, UI,
and older page-oriented clients.

```bash
curl -sS -X POST "$LORE_URL/api/memory/capture" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The demo service was started locally on port 8078.",
    "agent_name": "quickstart",
    "lane": "project",
    "task_id": "quickstart",
    "metadata": {
      "title": "Demo deploy note",
      "related_pages": ["projects/demo"],
      "confidence": "high"
    },
    "provenance": {"sources": ["docs/quickstart.md"]}
  }'
```

List draft captures:

```bash
curl -sS "$LORE_URL/api/captures?status=draft"
```

## Python SDK

Install the SDK from the repository (package name `axis-lore-sdk`):

```bash
pip install -e sdk/python
```

```python
from lore_sdk import LoreClient, MemoryProvider

client = LoreClient(base_url="http://localhost:8078")
memory = MemoryProvider(base_url="http://localhost:8078")
client.upsert_page(
    "projects/sdk-demo",
    "---\ntitle: SDK Demo\nkind: project\nvisibility: internal\n---\n\n# SDK Demo\n\nCreated from Python.\n",
)
hits = client.search("SDK Demo")
memory.capture(
    "The Python SDK created and searched a page.",
    agent_name="quickstart",
    lane="project",
    task_id="quickstart",
    metadata={
        "title": "Python SDK quickstart",
        "related_pages": ["projects/sdk-demo"],
        "confidence": "high",
        "sources": ["docs/quickstart.md"],
    },
)
```

## TypeScript SDK

Build the SDK from the repository (it is not published to npm yet; `import` from
the built output or add it as a local file dependency):

```bash
cd sdk/typescript
npm install
npm run build
```

```ts
import { LoreClient } from "axis-lore-sdk";

const client = new LoreClient({ baseUrl: "http://localhost:8078" });

await client.upsertPage(
  "projects/ts-demo",
  "---\ntitle: TypeScript Demo\nkind: project\nvisibility: internal\n---\n\n# TypeScript Demo\n\nCreated from TypeScript.\n",
);

const hits = await client.search("TypeScript Demo");
await client.createCapture({
  title: "TypeScript SDK quickstart",
  body: "The TypeScript SDK created and searched a page.",
  source: "docs/quickstart.md",
});
```
