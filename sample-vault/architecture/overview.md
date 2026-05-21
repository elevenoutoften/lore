---
title: Architecture Overview
kind: architecture
visibility: public
summary: High-level architecture for the Lore service and sample vault.
source_paths:
  - services/lore/lore_app/main.py
  - services/lore/lore_app/repository.py
---
# Architecture Overview

Lore exposes a FastAPI application over a Markdown repository. The main service
composition happens in `services/lore/lore_app/main.py`, while page storage and
frontmatter parsing live in `services/lore/lore_app/repository.py`.

## Runtime Flow

Requests enter the FastAPI app, pass through auth, observability, write rate
limits, and security headers, then reach route handlers. Page writes update the
Markdown file, search index, vector index, graph cache, and audit log.

## Major Areas

- [[Components|architecture/components]] describes the app modules.
- [[API Usage|guides/api-usage]] shows common REST calls.
- [[Search|guides/search]] explains repository, full-text, semantic, and graph
  retrieval.
- [[Agent Memory Decision|decisions/agent-memory-policy]] captures the demo
  policy for drafts and promotion.

## Embedded Code References

- `services/lore/lore_app/main.py:create_app`
- `services/lore/lore_app/repository.py:LoreRepository`
- `services/lore/lore_app/search_index.py:LoreSearchIndex`
- `services/lore/lore_app/rag/hybrid_retrieval.py:hybrid_retrieve`
