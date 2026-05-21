---
title: Search
kind: guide
visibility: public
summary: Full-text, semantic, and graph search patterns for Lore.
---
# Search

Lore supports several retrieval paths so agents can find the right context
before editing or answering.

## Repository Search

```bash
curl -sS "$LORE_URL/api/search?q=model%20gateway&limit=5"
```

This searches page IDs, titles, tags, summaries, and body content.

## Full-Text and BM25

```bash
curl -sS "$LORE_URL/api/search/fts?q=gateway"
curl -sS "$LORE_URL/api/search/bm25?q=gateway"
```

Use these endpoints when exact terms matter.

## Semantic Retrieval

```bash
curl -sS -X POST "$LORE_URL/api/rag/retrieve" \
  -H "Content-Type: application/json" \
  -d '{"query":"which service brokers model runtime calls","limit":5}'
```

Hybrid retrieval combines search, vector chunks, and graph context. It is useful
for agent task planning.

## Graph Search

```bash
curl -sS "$LORE_URL/api/links"
curl -sS "$LORE_URL/api/pages/services/api-gateway/links"
```

Graph lookups show how pages connect. Try [[API Gateway|services/api-gateway]]
and [[Service Dashboard|services/service-dashboard]].
