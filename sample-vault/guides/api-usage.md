---
title: API Usage
kind: guide
visibility: public
summary: Practical curl examples for common Lore REST workflows.
---
# API Usage

Set the base URL:

```bash
export LORE_URL=http://localhost:8078
```

## Create a Page

```bash
curl -sS -X PUT "$LORE_URL/api/pages/services/demo" \
  -H "Content-Type: application/json" \
  -d '{"content":"---\ntitle: Demo Service\nkind: service\nvisibility: internal\nsummary: Demo page.\n---\n\n# Demo Service\n\nLinked to [[Architecture|architecture/overview]].\n"}'
```

## Read and Render

```bash
curl -sS "$LORE_URL/api/pages/services/demo"
curl -sS "$LORE_URL/api/pages/services/demo/rendered"
curl -sS "$LORE_URL/api/pages/services/demo/links"
```

## Search

```bash
curl -sS "$LORE_URL/api/search?q=architecture&limit=5"
```

For retrieval workflows, continue with [[Search|guides/search]].

## Capture an Observation

```bash
curl -sS -X POST "$LORE_URL/api/capture" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Demo service note",
    "observation": "The demo service page links back to the architecture overview.",
    "related_pages": ["services/demo", "architecture/overview"],
    "confidence": "high",
    "sources": ["services/lore/sample-vault/guides/api-usage.md"]
  }'
```

See [[Capture Workflow|guides/capture-workflow]] for consolidation and promotion.
