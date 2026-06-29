---
title: Flow
kind: service
visibility: internal
status: active
summary: Task orchestration backbone — emits the provenance events Lore records.
tags:
  - orchestration
  - tasks
owner: platform-team
confidence: high
reviewed_at: 2026-06-12
epistemic_status: operator_declared
source_task: flow_000004
actor: atlas
---

Flow schedules and runs the agent tasks that produce knowledge. Whenever a task touches a page, Flow emits a provenance event — task id, actor, tool calls — that [[services/lore|Lore]] attaches to the resulting content.

## Role

Flow is the source of the `source_task` field you see on most pages. It does not store knowledge itself; it is the conveyor belt.

## Provenance events

- task lifecycle (created → done)
- tool-call records
- actor attribution
- policy checks applied via [[services/gateway|the gateway]]
