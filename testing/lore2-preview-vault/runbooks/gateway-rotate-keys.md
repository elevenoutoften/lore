---
title: Gateway Rotate Keys
kind: runbook
visibility: internal
status: active
summary: Rotate the gateway signing keys without dropping live traffic.
tags:
  - ops
  - security
owner: runtime-team
confidence: high
reviewed_at: 2026-06-05
epistemic_status: operator_declared
actor: atlas
---

Zero-downtime rotation for [[services/gateway|Axis Gateway]] signing keys.

## Steps

1. Publish the new key as secondary.
1. Wait one propagation cycle.
1. Promote new to primary, demote old to secondary.
1. Revoke the old key after the grace window.
