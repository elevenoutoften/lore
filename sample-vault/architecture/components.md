---
title: Components
kind: architecture
visibility: public
summary: Component map for repository, API, search, graph, capture, and SDKs.
---
# Components

## Repository

`LoreRepository` reads and writes Markdown pages under the configured content
directory. It normalizes page IDs, parses frontmatter, lists pages, and performs
simple repository search.

## API

The FastAPI app exposes page, search, graph, capture, lint, MCP, and browser
routes. See [[API Usage|guides/api-usage]] for examples.

## Search and Retrieval

Search combines repository scoring, SQLite full-text search, vector chunks, and
graph context. See [[Search|guides/search]].

## Capture

Captures write draft observations into inbox or agent note namespaces. Reviewed
captures can be promoted into canonical pages. See [[Capture Workflow|guides/capture-workflow]].

## Services

The sample vault includes [[API Gateway|services/api-gateway]] and
[[Service Dashboard|services/service-dashboard]] to demonstrate service documentation with
cross-links back to [[Architecture Overview|architecture/overview]].
