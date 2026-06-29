---
title: Lore Reindex
kind: runbook
visibility: internal
status: active
summary: Rebuild the Lore search and link indexes after a bulk import.
tags:
  - ops
  - index
owner: platform-team
confidence: high
reviewed_at: 2026-06-09
epistemic_status: operator_declared
source_task: flow_000201
actor: atlas
---

Run this after any bulk import or migration that touches more than ~50 pages. Reindex is safe to run live but briefly degrades search ranking.

## When to run

After bulk imports, after schema migrations, or when backlinks look stale in [[services/lore|Lore]].

## Steps

1. Snapshot the current index.
1. Drain the write queue.
1. Trigger `lore reindex --full`.
1. Rebuild the link graph and [[concepts/bitemporal-claims|claim validity]] windows.

## Verify

Spot-check that backlinks resolve and search returns expected top hits.
