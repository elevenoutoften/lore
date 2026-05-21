# Roadmap

Lore is in active build-out toward a standalone beta release. This roadmap
tracks the next documented phases that make the repository easier to operate,
review, and extend.

The scope here is intentionally small:

- It covers the current planned phases for memory foundation and security
  hardening.
- It points back to the docs that already define today's behavior.
- It does not describe speculative features beyond the current backlog.

## Current Baseline

Lore already documents the core operator and agent workflows:

- [Quickstart](quickstart.md) for local setup, page CRUD, search, and capture.
- [API Reference](api-reference.md) for HTTP endpoints and payloads.
- [Agent Integration](agent-integration.md) for agent read, search, and capture
  patterns.
- [Capture Templates](capture-templates.md) for draft memory intake.
- [Distillation](distillation.md) for today's capture consolidation workflow.
- [Security](security.md) and [Deployment](deployment.md) for runtime controls.

These guides describe the current product surface. The phases below describe
the next implementation work needed before broader beta use.

## Phase 1: Memory Foundation

Goal: make captured agent memory durable, structured, and easier to consolidate
into canonical Lore pages.

Planned work:

- Consolidation runner for moving draft captures through a repeatable memory
  processing flow.
- Structured metadata for captures and related memory records so downstream
  automation can reason about provenance and state.
- Persistence for memory-processing state so consolidation can survive process
  restarts and normal operations.
- Freshness signals that help agents and operators identify which memory needs
  review or refresh.
- End-to-end coverage for the memory flow from capture intake through
  consolidation behavior.

Relevant docs today:

- [Quickstart](quickstart.md) shows current capture creation and listing.
- [Capture Templates](capture-templates.md) defines the draft capture format.
- [Agent Integration](agent-integration.md) describes when agents should capture
  observations versus promote confirmed knowledge.
- [Distillation](distillation.md) documents the existing daily consolidation
  workflow that Phase 1 builds on.
- [API Reference](api-reference.md) is the baseline for any API-facing memory
  changes.

Phase 1 outcome:

Lore keeps Markdown as the source of truth while gaining a more reliable memory
foundation for agent-driven consolidation and review.

## Phase 2: Security Hardening

Goal: tighten the standalone deployment model so Lore fails closed and records
enough context for accountable operation.

Planned work:

- Auth behavior that fails closed when deployment auth is missing, invalid, or
  misconfigured.
- Deployment wiring that keeps authentication and runtime protections aligned
  across local, proxy, and service setups.
- Actor attribution for protected actions so operational changes can be traced
  to a specific authenticated principal.
- Privacy scanning to catch sensitive content before or during normal Lore
  workflows.

Relevant docs today:

- [Security](security.md) documents current auth modes, headers, rate limiting,
  and validation behavior.
- [Deployment](deployment.md) covers reverse proxy and service configuration for
  standalone environments.
- [Beta Release Checklist](beta-release-checklist.md) lists the release checks
  that Phase 2 should help satisfy consistently.

Phase 2 outcome:

Lore is easier to run as a standalone service with clearer auth guarantees,
better operational traceability, and stronger privacy controls.

## Beta Release Milestone

Goal: ship a Lore beta with documented workflows, release checks, and the core
memory and security phases in place.

The beta milestone depends on:

- Phase 1 delivering a stable memory foundation for capture and consolidation
  workflows.
- Phase 2 delivering hardened standalone deployment and security behavior.
- Release verification through the
  [Beta Release Checklist](beta-release-checklist.md).

Related docs:

- [Beta Release Checklist](beta-release-checklist.md)
- [Migration Guide](migration-guide.md)
- [Index](index.md)

As implementation lands, this roadmap should stay short and move items into the
relevant operating guides instead of becoming a second source of truth.
