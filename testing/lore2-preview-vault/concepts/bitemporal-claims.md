---
title: Bitemporal Claims
kind: concept
visibility: public
status: active
summary: Every claim carries when it was observed and the window it is actually true.
tags:
  - time
  - claims
  - model
owner: platform-team
confidence: high
reviewed_at: 2026-06-14
epistemic_status: operator_declared
actor: nyx
---

A claim has two independent timelines: when the system _learned_ it, and when it is _true in the world_.

## Two clocks

- `observed_at` — when we recorded it.
- `valid_from` / `valid_until` — the world-truth window; a null end means still valid.

This lets a superseding claim invalidate an old one without deleting history.
