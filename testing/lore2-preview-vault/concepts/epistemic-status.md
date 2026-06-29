---
title: Epistemic Status
kind: concept
visibility: public
status: active
summary: How a stored fact was obtained — declared, retrieved, inferred, assumed, or hearsay.
tags:
  - trust
  - model
owner: platform-team
confidence: high
reviewed_at: 2026-06-15
epistemic_status: operator_declared
actor: nyx
---

Epistemic status answers a single question: _how do we know this?_ It is the lightest-weight trust signal in the system.

## The five values

- **operator_declared** — a human asserted it.
- **retrieved** — pulled from a cited source.
- **inferred** — derived by an agent.
- **assumption** — working hypothesis.
- **hearsay** — unverified.

Pair it with [[concepts/bitemporal-claims|bi-temporal validity]] to know both how and when a fact holds.
