---
title: Adopt Context Graph
kind: decision
visibility: internal
status: accepted
summary: Adopt the typed context graph as the canonical retrieval model over the link graph.
tags:
  - architecture
  - graph
  - retrieval
owner: platform-team
confidence: high
reviewed_at: 2026-06-18
epistemic_status: operator_declared
actor: nyx
decided_at: 2026-06-18
deciders:
  - nyx
  - platform-team
alternatives:
  - Keep the link graph only
  - Adopt an external graph DB
---

The link graph only knew about pages and wikilinks. It could not represent _why_ a fact existed — the claim, the trace, the plan that produced it.

## Context

Retrieval quality plateaued because the graph was too thin. We needed claims, traces, actors, and policies as first-class nodes.

## Decision

Adopt the typed context graph — twelve node types — as the canonical model. The link graph becomes a derived view. [[services/lore|Lore]] builds both; the reader keeps the link view, the explorer uses the full graph.

## Alternatives considered

- **Keep the link graph only** — rejected, too thin for provenance.
- **Adopt an external graph DB** — rejected, operational weight not justified yet.
