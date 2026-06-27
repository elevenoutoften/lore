# Roadmap

Lore is in active build-out toward a standalone beta release. This roadmap
tracks the remaining work between today's baseline and that release.

The scope here is intentionally small:

- It records what the memory-foundation and security-hardening phases already
  delivered and points to the operating guides that own that behavior.
- It lists only genuinely-unbuilt work as forward roadmap.
- It does not describe speculative features as committed direction.

## Current Baseline

Lore already documents the core operator and agent workflows:

- [Quickstart](quickstart.md) for local setup, page CRUD, search, and capture.
- [API Reference](api-reference.md) for HTTP endpoints and payloads.
- [Agent Integration](agent-integration.md) for agent read, search, and capture
  patterns.
- [Capture Templates](capture-templates.md) for draft memory intake.
- [Consolidation](consolidation.md) for extraction, patch planning, and
  automated knowledge maintenance.
- [Distillation](distillation.md) for the session-to-daily-note workflow.
- [Security](security.md) and [Deployment](deployment.md) for runtime controls.
- [Concurrency Model](concurrency.md) for the SQLite/WAL write model, measured
  10-writer ceiling, and Postgres/Kuzu upgrade triggers.

These guides describe the current product surface and own the behavior the
phases below delivered.

## Delivered / Current Baseline

The memory-foundation and security-hardening phases have shipped. Each item now
lives in an operating guide rather than this roadmap.

### Memory foundation

- Scheduled consolidation runner that moves draft captures through extraction,
  patch planning, and bounded safe auto-apply — on the capture-triggered path,
  a background maintenance schedule, and the `lore-admin consolidate` CLI.
  See [Consolidation](consolidation.md).
- Structured metadata and provenance for captures and downstream memory records
  (typed `provenance`, actor, lane, confidence), so automation can reason about
  where a record came from and its state.
  See [Agent Memory Contract](agent-memory-contract.md).
- Persistent memory-processing state in the ledger, so extraction candidates,
  patch plans, run records, and worker traces survive process restarts.
  See [Consolidation](consolidation.md).
- Freshness and review signals: recency/salience-weighted recall and
  promotion/review state on distilled notes help agents and operators see which
  memory needs review or refresh.
  See [Agent Memory Contract](agent-memory-contract.md) and
  [Distillation](distillation.md).
- End-to-end behavior from capture intake through consolidation, exercised by
  the test suite so the capture-to-durable path stays covered.

### Security hardening

- Fail-closed auth: bearer and basic modes refuse to start without a non-empty
  secret, and Lore refuses to start when a known placeholder secret is combined
  with a non-loopback bind address.
  See [Security](security.md).
- Deployment auth wiring that keeps authentication and trusted-proxy behavior
  aligned across local, proxy, and service setups.
  See [Deployment](deployment.md) and [Security](security.md).
- Actor attribution for protected actions: the authenticated request resolves to
  an actor and role used for attribution, actor-scoped recall, audit logs, and
  per-actor write budgets.
  See [Security](security.md) and [Agent Memory Contract](agent-memory-contract.md).

## Forward Roadmap

The remaining committed work before broader beta use is release verification.

- Beta publish signoff: complete the
  [Beta Release Checklist](beta-release-checklist.md) against a deployed
  instance and confirm the documented workflows, auth guarantees, and
  operational behavior hold end to end.

## Exploratory (not yet decided)

These ideas are under consideration but are **not** committed work and have no
implementation today:

- A consolidation/recall benchmark harness to measure extraction and recall
  quality against fixed fixtures.
- Slimming the product surface (API, MCP, and UI) around the core memory
  contract.
- Optional Kuzu/graph-backend and PostgreSQL storage/index spikes remain gated
  by the measured findings in [Concurrency Model](concurrency.md): Kuzu waits
  for graph rebuild pressure, and PostgreSQL waits for hosted multi-tenant or
  multi-process writer requirements.

## Beta Release Milestone

Goal: ship a Lore beta with documented workflows, release checks, and the
delivered memory and security baseline in place.

The beta milestone depends on:

- The delivered memory foundation and security baseline above continuing to hold.
- Release verification through the
  [Beta Release Checklist](beta-release-checklist.md).

Related docs:

- [Beta Release Checklist](beta-release-checklist.md)
- [Index](index.md)

As implementation lands, this roadmap should stay short and move items into the
relevant operating guides instead of becoming a second source of truth.
