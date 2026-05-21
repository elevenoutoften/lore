# Lore Capture Templates

Lore captures are Markdown pages with YAML frontmatter. The generated template
is the canonical intake format for observations that still need consolidation
before they are accepted as Lore truth.

## Standard Inbox Capture

Page ID format: `inbox/YYYY-MM-DD/<slug>`

Use `namespace=inbox` for the shared intake queue. Inbox captures are rough
agent observations that should be consolidated by agents or automation before
they become canonical page content.

Required body field: `observation`

Frontmatter fields:

- `title`
- `kind`
- `visibility`
- `status`
- `summary`
- `tags`
- `captured_at`
- `confidence`
- `source_task`
- `suggested_target_page`
- `related`
- `sources`
- `source_paths`
- `source_urls`
- `evidence`

Example:

```markdown
---
title: Template test
kind: capture
visibility: internal
status: draft
summary: Rough agent memory capture; not canonical truth.
tags: [capture, agent-memory]
captured_at: 2026-05-01T12:00:00+00:00
confidence: high
source_task: flow_000114
suggested_target_page: services/lore
related:
  - services/lore
sources:
  - docs/capture-templates.md
source_paths:
  - services/lore/lore_app/capture.py
source_urls:
  - https://example.com
evidence: Verified by reading source code
---

# Template test

> Captured memory is rough intake for agent consolidation, not final Lore truth.

## Observation

Template convention verification.

## Source Task

flow_000114

## Sources

- docs/capture-templates.md

## Source Paths

- services/lore/lore_app/capture.py

## Source URLs

- https://example.com

## Evidence

Verified by reading source code

## Related Pages

- [[services/lore]]

## Suggested Target

[[services/lore]]
```

## Agent Notes

Page ID format: `notes/<agent-slug>/YYYY-MM-DD/<slug>`

Use `namespace=notes` for agent-scoped running notes and session logs. Agent notes use the same frontmatter and body template as inbox captures, but the page ID is scoped by the agent slug.

Example:

```markdown
---
title: Indexing follow-up
kind: capture
visibility: internal
status: draft
summary: Rough agent memory capture; not canonical truth.
tags: [capture, agent-memory]
captured_at: 2026-05-01T13:05:00+00:00
confidence: medium
---

# Indexing follow-up

> Captured memory is rough intake for agent consolidation, not final Lore truth.

## Observation

Agent codex noted that capture pages can stay in draft until automated
consolidation selects a target page.
```

## Decision Records

Page ID format: `decisions/<slug>`

Decision records are ADR-style canonical pages for choices that should be preserved with context, deciders, and consequences.

Example:

```markdown
---
title: "Decision Title"
kind: decision
visibility: internal
summary: "One-line summary of the decision."
status: proposed
decided_at: "2026-05-02"
deciders:
  - name
sources:
  - source
alternatives:
  - option A
  - option B
---

# Decision Title

## Context
Why this decision is needed.

## Decision
What was decided.

## Consequences
What changes as a result.
```

## Daily Note Convention

Page ID format: `notes/<agent-slug>/YYYY-MM-DD/daily`

Daily notes are a naming convention, not a separate built-in capture type. Use them for end-of-session summaries of what was learned or done.

Example:

```markdown
---
title: Daily
kind: capture
visibility: internal
status: draft
summary: Rough agent memory capture; not canonical truth.
tags: [capture, agent-memory]
captured_at: 2026-05-01T23:55:00+00:00
confidence: medium
related:
  - services/lore
---

# Daily

> Captured memory is rough intake for agent consolidation, not final Lore truth.

## Observation

- Added capture template documentation.
- Verified the capture API keeps new captures in draft status.
- Confirmed accepted captures can point to canonical pages through promoted_to.

## Related Pages

- [[services/lore]]
```

## Session Note Convention

Page ID format: `notes/<agent-slug>/YYYY-MM-DD/session-<HHMM>`

Session notes are a naming convention for per-session memory dumps. Use them when a session produced several useful observations that should remain grouped.

Example:

```markdown
---
title: Session 1430
kind: capture
visibility: internal
status: draft
summary: Rough agent memory capture; not canonical truth.
tags: [capture, agent-memory]
captured_at: 2026-05-01T14:30:00+00:00
confidence: high
source_task: flow_000114
related:
  - services/lore
  - runbooks/writing-lore-pages
sources:
  - services/lore/docs/capture-templates.md
---

# Session 1430

> Captured memory is rough intake for agent consolidation, not final Lore truth.

## Observation

The capture template now has documented inbox, agent note, daily note, and session note conventions.

## Source Task

flow_000114

## Sources

- services/lore/docs/capture-templates.md

## Related Pages

- [[services/lore]]
- [[runbooks/writing-lore-pages]]
```

## Capture Status Lifecycle

Captures move through a small consolidation lifecycle:

- `draft`: default state for newly captured memory.
- `review`: queued for focused agent consolidation or manual audit.
- `accepted`: useful content was promoted or incorporated into canonical Lore.
- `rejected`: reviewed and intentionally not used.
- `archived`: retained for history but no longer active in review.

When a capture is accepted through promotion, the capture frontmatter gets `promoted_to` with the canonical target page ID.

```yaml
status: accepted
promoted_to: services/lore
```

## Frontmatter Field Reference

| Field | Type | Required | Description | Example values |
| --- | --- | --- | --- | --- |
| `title` | string | yes | Human-readable capture title. | `Template test` |
| `kind` | string | yes | Page kind. Captures use `capture`. | `capture` |
| `visibility` | string | yes | Visibility scope for the page. | `internal` |
| `status` | string | yes | Consolidation lifecycle status. | `draft`, `review`, `accepted`, `rejected`, `archived` |
| `summary` | string | yes | Fixed warning that captures are rough memory. | `Rough agent memory capture; not canonical truth.` |
| `tags` | list[string] | yes | Capture classification tags. | `[capture, agent-memory]` |
| `captured_at` | datetime string | yes | UTC timestamp when the capture was generated. | `2026-05-01T12:00:00+00:00` |
| `confidence` | string | yes | Confidence level supplied by the agent or `unknown`. | `high`, `medium`, `low`, `unknown` |
| `source_task` | string | no | Task, ticket, or flow ID that produced the capture. | `flow_000114`, `FLOW-123` |
| `suggested_target_page` | string | no | Canonical page that may receive the capture content. | `services/lore` |
| `related` | list[string] | no | Related Lore page IDs. | `[services/lore, runbooks/writing-lore-pages]` |
| `sources` | list[string] | no | Human-readable source references. | `[docs/capture-templates.md]` |
| `source_paths` | list[string] | no | Local repository paths used as evidence. | `[services/lore/lore_app/capture.py]` |
| `source_urls` | list[string] | no | HTTP or HTTPS source URLs. | `[https://example.com]` |
| `evidence` | string | no | Short explanation of how the observation was verified. | `Verified by reading source code` |
| `promoted_to` | string | no | Canonical page ID created or updated from an accepted capture. | `services/lore` |
