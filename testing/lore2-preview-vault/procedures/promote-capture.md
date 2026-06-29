---
title: Promote Capture
kind: procedure
visibility: internal
status: validated
summary: Turn a raw capture into a reviewed Lore page.
tags:
  - workflow
  - capture
owner: platform-team
confidence: high
reviewed_at: 2026-06-16
epistemic_status: operator_declared
policies_applied:
  - policy_pii_block
actor: nyx
trigger: A capture is marked promote-ready
---

Captures never appear in the reader. This procedure is how a capture earns its way into the canonical base.

## Trigger

A capture is marked `promote-ready` by an agent or a curator.

## Steps

1. Resolve a target page in [[services/lore|Lore]].
1. Assign an [[concepts/epistemic-status|epistemic status]].
1. Run the PII policy gate.
1. Open a patch plan for human review.
