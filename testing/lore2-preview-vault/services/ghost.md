---
title: Ghost
kind: service
visibility: internal
status: draft
summary: Ephemeral sandbox runner for untrusted agent code.
tags:
  - sandbox
  - runtime
owner: runtime-team
confidence: medium
reviewed_at: 2025-12-01
epistemic_status: retrieved
stale_after: 2026-04-01
actor: atlas
---

Ghost runs untrusted code in disposable microVMs. This page predates the Q2 runtime migration and several claims here have since been invalidated.

## Status

Ghost no longer auto-scales on its own; scaling now routes through [[services/gateway|Axis Gateway]]. Treat the autoscaling section as historical until reviewed.
