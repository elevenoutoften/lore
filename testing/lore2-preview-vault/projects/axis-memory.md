---
title: Axis Memory
kind: project
visibility: internal
status: active
summary: The umbrella effort to give Axis agents a durable, reviewable memory.
tags:
  - axis
  - memory
  - platform
owner: platform-team
confidence: high
reviewed_at: 2026-06-15
epistemic_status: operator_declared
source_task: flow_000088
actor: nyx
---

**Axis Memory** is the program that turns the scattered output of the agent fleet — decisions, runbooks, captured observations — into a single base of knowledge a human can actually read and trust.

## Overview

Two services do the heavy lifting: [[services/lore|Lore]] stores and serves canonical pages, while [[services/flow|Flow]] feeds it a stream of task provenance. The 2026 reset is captured in [[decisions/adopt-context-graph|the decision to adopt the typed context graph]].

## Scope

In scope: the reader wiki, the knowledge graph, and the review workflow. Out of scope for the human surface: the capture-review queue and policy console, which live in the agent-ops tooling.

### The stack

- Storage & rendering — [[services/lore|Lore]]
- Task provenance — [[services/flow|Flow]]
- Edge auth — [[services/gateway|Axis Gateway]]
