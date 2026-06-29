---
title: Gateway
kind: service
visibility: internal
status: active
summary: Edge authentication and routing for every Axis service call.
tags:
  - auth
  - routing
  - edge
owner: platform-team
confidence: high
reviewed_at: 2026-06-14
epistemic_status: operator_declared
policies_applied:
  - policy_pii_block
actor: atlas
---

Axis Gateway terminates auth at the edge and routes calls to the right service. Every request carries an actor identity that downstream services — including [[services/lore|Lore]] — use for scoping.

## Routing

Routing is policy-aware: a call can be rejected before it reaches a service if a governance policy denies it.

## Key rotation

Rotation is operational and documented in [[runbooks/gateway-rotate-keys|the rotation runbook]].
