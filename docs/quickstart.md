# Quickstart

This guide starts a local Lore server, writes the first page, searches it, and
captures draft agent memory.

## Install

Install from the Python package name used for distribution:

```bash
pip install lore-app
```

For local development from this repository:

```bash

python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
LORE_CONTENT_DIR=./sample-vault uvicorn lore_app.asgi:app --reload --port 8078
```

Run with Docker:

```bash
docker build -t lore-app .
docker run --rm -p 8078:8000 \
  -v "$PWD/lore:/data/pages" \
  lore-app
```

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

Captures store draft observations in the vault for autonomous consolidation.

```bash
curl -sS -X POST "$LORE_URL/api/capture" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Demo deploy note",
    "observation": "The demo service was started locally on port 8078.",
    "source_task": "quickstart",
    "related_pages": ["projects/demo"],
    "confidence": "high",
    "sources": ["docs/quickstart.md"]
  }'
```

List draft captures:

```bash
curl -sS "$LORE_URL/api/captures?status=draft"
```

## Python SDK

```python
from lore_sdk import LoreClient

client = LoreClient(base_url="http://localhost:8078")
client.upsert_page(
    "projects/sdk-demo",
    "---\ntitle: SDK Demo\nkind: project\nvisibility: internal\n---\n\n# SDK Demo\n\nCreated from Python.\n",
)
hits = client.search("SDK Demo")
client.create_capture(
    title="Python SDK quickstart",
    body="The Python SDK created and searched a page.",
    source="docs/quickstart.md",
    related_pages=["projects/sdk-demo"],
    confidence="high",
)
```

## TypeScript SDK

```ts
import { LoreClient } from "@axis-love/lore-sdk";

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
