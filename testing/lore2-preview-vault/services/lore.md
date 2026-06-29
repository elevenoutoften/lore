---
title: Lore
kind: service
visibility: internal
status: draft
summary: Canonical knowledge base for the Axis projects — stores, renders, and links every page.
tags:
  - knowledge
  - axis
  - wiki
owner: platform-team
confidence: high
reviewed_at: 2026-06-10
epistemic_status: operator_declared
sources:
  - https://axis.internal/specs/lore
source_task: flow_000123
decision_id: decisions/adopt-context-graph
policies_applied:
  - policy_pii_block
actor: nyx
---

Lore is the system of record for everything the Axis agents have established as true. Each page is markdown with typed frontmatter; the service renders it to safe HTML, resolves `[[wiki-links]]`, and maintains the link graph that powers backlinks and the [[decisions/adopt-context-graph|context graph]].

## What it stores

Pages come in eight kinds. Captures — raw agent observations — are held separately and never surface in the reader; they are promoted into real pages through the [[procedures/promote-capture|capture-promotion procedure]].

| Kind | Purpose |
| --- | --- |
| service | A running system component |
| decision | A recorded choice with alternatives |
| runbook | Operational steps |
| concept | A definition or model |

## Rendering

The renderer is CommonMark plus tables, strikethrough, and linkify. Raw HTML is disabled and output is allowlist-sanitized, so a page is always safe to inject. A leading title heading is stripped — the chrome owns the title.

### Links & backlinks

Every internal link is resolved against the corpus. Unresolved links — like [[services/ghost-legacy|Ghost Legacy]] — are flagged as broken so a curator can fix or create them. Backlinks are derived by reversing the edge set.

## Retrieval

Retrieval is organised into [[concepts/retrieval-lanes|lanes]], and every stored fact carries an [[concepts/epistemic-status|epistemic status]] and [[concepts/bitemporal-claims|bi-temporal validity]]. See the full spec at [axis.internal/specs/lore](https://axis.internal/specs/lore).
