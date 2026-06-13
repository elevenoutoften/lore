# Lore UI/UX Design Brief

Snapshot date: 2026-06-01

This brief is for the design team working on a proper Web UI for Lore. It
summarizes what the product does, what humans are expected to do in the UI, and
which upcoming features from the roadmap should shape the design.

## One-Screen Summary

Lore is a Markdown-backed knowledge system for humans, coding agents, and
automation. Its core promise is simple: canonical project knowledge lives in
ordinary wiki pages, while agents and tools can read, search, capture, connect,
audit, and maintain that knowledge without replacing the Markdown source of
truth.

The two most important human UI modes are:

1. **Wiki reading**: browse and read pages like a normal internal wiki.
2. **Graph exploration**: see how pages, captures, decisions, sources, and
   reasoning artifacts connect.

Everything else should support those two modes: search, relationship panels,
capture review, knowledge health checks, provenance, and admin/developer setup.

## Product Model

### Canonical Pages

Pages are Markdown files with frontmatter metadata. Page IDs are path-like, for
example `services/lore`, `runbooks/deploy-lore`, or `projects/example-project`.

Important page metadata:

- `title`: human page title.
- `kind`: page type such as `project`, `service`, `runbook`, `decision`,
  `procedure`, `capture`, or `daily-note`.
- `visibility`: usually `public`, `internal`, or `private`.
- `status`: lifecycle state such as active, draft, reviewed, stale, or archived.
- `summary`, `tags`, `sources`, `confidence`, and provenance fields.

Design implication: the page reader should feel like a clear wiki first, with
metadata and provenance available without overwhelming the reading experience.

### Links and Graphs

Lore supports normal Markdown links and wiki-style links:

- `[Pixl](services/pixl)`
- `[[services/pixl]]`
- `[[Pixl|services/pixl]]`

The app can show outgoing links, backlinks, missing links, source-reference
edges, and graph analytics. There are two related graph concepts:

- **Link graph**: page-to-page wiki links.
- **Context graph**: a richer graph over pages, captures, claims, sources,
  traces, policies, decisions, actors, and patch plans.

Design implication: the graph should not be a decorative network. It should help
users answer "what is connected to this?" and "why does this page matter?"

### Captures

Captures are rough observations written by agents, CI bots, or humans. They are
not canonical truth until promoted or consolidated into normal pages.

Capture statuses:

- `draft`
- `review`
- `accepted`
- `rejected`
- `archived`

Capture metadata can include source task, related pages, suggested target page,
confidence, evidence, sources, actor, lane, and time fields.

Design implication: captures should look like an inbox or review queue, not like
regular finished wiki pages.

### Provenance, Policies, and Traces

Lore tracks where knowledge came from and why automated decisions happened:

- Provenance: sources, evidence, actor, lane, timestamps, model metadata.
- Policies: rules that decide whether automated patch plans can apply or need
  review.
- Traces: audit-grade explanations of important agent decisions, without storing
  hidden model chain-of-thought.
- Precedents: previous similar decisions or situations.

Design implication: provenance should be visible where trust matters, but it
should be summarized by default. Most users need "source, confidence, last
reviewed, and why this is connected" before they need full audit detail.

## Existing Human Web Surfaces

These routes exist today and show the current product surface. Some are useful
human workflows; others are more like developer/debug pages.

| Area | Current Route | What The User Does |
| --- | --- | --- |
| Wiki reader | `/`, `/{page_id}`, `/pages/{page_id}` | Browse the page list, filter by kind/visibility, read rendered Markdown, use TOC, inspect metadata, sources, backlinks, outgoing links, and broken links. |
| Search | `/search?q=...` | Search pages, filter by kind, inspect snippets and scores, open a result. |
| Link graph | `/graph` | View a force-style graph, hover nodes, click a page, open JSON/stats links. |
| Capture queue | `/captures` | Review captured agent memory by status, confidence, source task, related pages, and suggested target. |
| Lint dashboard | `/lint` | See knowledge-quality issues grouped by severity and rule. |
| Heartbeat review | `/heartbeat` | Review stale pages, contradictions, low-confidence pages, expired facts, procedure issues, and memory health counts. |
| Procedure candidates | `/procedures` | See repeated capture patterns that may become reusable procedures. |
| API keys | `/api-keys` | Create, view, and revoke Lore API keys for agents and services. |
| RAG debug | `/rag?q=...` | Test retrieval results with scores and source badges. This is mainly developer-facing. |
| Embed widget | `/embed?...` | Render a page, search view, or capture list inside another app. |
| Raw APIs | `/api/*`, `/mcp` | Agent, integration, and developer surfaces. These should not dominate the main human navigation. |

Current gaps to be aware of:

- Page CRUD exists through the API, but there is not yet a polished page editor.
- Capture status changes and promotion exist through the API, but the current
  capture UI is mostly read/review.
- Graph visualization exists, but it has few exploration controls.
- RAG and MCP are powerful, but should probably be hidden behind developer or
  advanced modes in a human UI.

## Core Human Jobs

### 1. Read Canonical Knowledge

User goal: "I want to understand this project/service/runbook/decision."

Expected UI:

- Clean page reader.
- Page title, summary, kind, status, visibility, updated date.
- Table of contents for long pages.
- Sources and confidence visible but not noisy.
- Links and backlinks nearby.
- Clear warning when a page is a draft capture, stale, low-confidence, or
  contradicted.

### 2. Find The Right Page

User goal: "I know roughly what I need, but not the page ID."

Expected UI:

- Prominent search.
- Filters for kind, visibility, status, tag, confidence, and maybe owner/lane.
- Results with title, summary, kind, status, snippet, and source/confidence
  hints.
- Easy path from search result to graph neighborhood.

### 3. Explore Connections

User goal: "Show me how this knowledge fits together."

Expected UI:

- Page-level relationship panel: outgoing links, backlinks, missing links,
  source refs, related captures, related decisions, related traces.
- Graph-level explorer: search node, filter by kind/status/confidence, focus on
  a selected node, expand neighbors, show path explanations, open page in a side
  panel.
- Distinct visual treatment for canonical pages, draft captures, decisions,
  runbooks, sources, policies, and traces.

### 4. Review Agent Memory

User goal: "Agents captured notes. What should become durable knowledge?"

Expected UI:

- Inbox-like capture queue.
- Status filters: draft, review, accepted, rejected, archived.
- Confidence and epistemic status badges.
- Source task, related pages, suggested target page, evidence, and source links.
- Compare capture to target page.
- Actions: accept, reject, archive, promote into target page, create target page,
  or request more evidence.

### 5. Maintain Knowledge Health

User goal: "What needs attention?"

Expected UI:

- Health dashboard with actionable queues rather than raw JSON.
- Stale pages, missing metadata, broken links, contradictions, low confidence,
  expired facts, and procedure issues.
- One issue should lead to the page, the evidence, and the fix/review action.
- "All clear" states should be explicit.

### 6. Understand Provenance

User goal: "Can I trust this?"

Expected UI:

- Compact trust summary on pages and search results.
- Sources, evidence, confidence, epistemic status, actor, lane, observed date,
  valid-from/valid-until where available.
- Full provenance drilldown for auditors or maintainers.
- Reasoning traces and policies shown as reviewable records, not hidden magic.

### 7. Manage Agent Access

User goal: "Let an agent or integration read/write Lore."

Expected UI:

- API key management.
- Role clarity: reader, writer, admin.
- Token is shown once on creation.
- Revoked keys remain visible as audit history.

## Suggested Information Architecture

Recommended primary navigation:

- **Wiki**: browse and read canonical pages.
- **Search**: find pages and context.
- **Graph**: explore relationships.
- **Inbox**: review captures and promotions.
- **Health**: lint, heartbeat, stale/contradiction queues.
- **Admin**: API keys, config, audit.
- **Developer**: API, MCP, SDKs, RAG debug, embed setup.

Design principle: keep API/MCP/debug endpoints out of the main path for normal
readers. They matter a lot for agents and developers, but they are not the main
human mental model.

## Priority Screen Set

### MVP Human UI

1. Wiki reader and page browser.
2. Search results.
3. Graph explorer.
4. Capture inbox.
5. Health dashboard.
6. API key admin.

### Strong V2 Candidates

1. Page editor with Markdown/frontmatter support.
2. Capture promotion and merge workflow.
3. Patch-plan review queue.
4. Provenance and trace explorer.
5. Precedent search.
6. Context graph path explanation view.
7. Extraction monitor for LLM extraction failures, dead letters, retries, and
   model/eval status.

## Important User Flows

### Flow A: Read A Wiki Page

1. User opens Wiki.
2. User browses or filters pages by kind.
3. User opens a page.
4. User reads summary and content.
5. User uses TOC, outgoing links, backlinks, and sources to continue.

### Flow B: Search To Page To Graph

1. User searches for a topic.
2. User scans result snippets and metadata.
3. User opens the most relevant page.
4. User jumps to the page's local graph or full graph view.
5. User explores adjacent pages and returns to the reader.

### Flow C: Broken Link To Stub

1. User sees a missing internal link on a page.
2. User inspects where the missing page is referenced.
3. User creates a stub page.
4. User or an agent fills the stub later.

### Flow D: Capture Review

1. User opens Inbox.
2. User filters to draft or review captures.
3. User opens a capture and checks confidence, source task, evidence, related
   pages, and suggested target.
4. User promotes, rejects, archives, or asks for more evidence.
5. Canonical page changes become visible in Wiki, Search, and Graph.

### Flow E: Maintenance Review

1. User opens Health.
2. User selects stale pages, contradictions, low confidence, broken links, or
   missing metadata.
3. User opens the affected page and evidence.
4. User fixes, promotes, archives, or leaves a review note.

### Flow F: Agent Setup

1. Admin opens API Keys.
2. Admin creates a reader/writer/admin key.
3. Admin copies the token once.
4. Integration uses REST, SDK, or MCP.
5. Admin can revoke the key later.

## Feature Inventory

### Current Or Implemented

- Markdown-backed wiki pages.
- Page list, page read, page create/update/delete through API.
- Rendered Markdown with TOC, tables, code blocks, sanitized HTML, and wiki
  links.
- Page-level outgoing links, backlinks, and missing links.
- Stub creation for missing pages.
- Search: repository search, full-text search, BM25, hybrid RAG retrieval.
- Link graph, enriched graph, graph stats, source-reference edges.
- Graph analytics for centrality, communities, and entry points.
- Captures, capture status transitions, promotion, capture digest.
- Daily distillation from session captures to daily notes.
- Heartbeat self-audit for stale pages, contradictions, low confidence, expired
  facts, and procedure issues.
- Lint checks for quality, stale content, contradictions, metadata, broken
  links, and orphan/low-confidence pages.
- Procedures and procedure candidates from repeated captures.
- Decisions and decision templates.
- Code references and code ingest.
- Consolidation runner, patch planning, policy gates, review-required plans, and
  rollback.
- Ledger claims, candidates, policy rules, and precedent search.
- Provenance and reasoning traces.
- API key management.
- MCP endpoint and tools.
- Python and TypeScript SDKs.
- Embeddable Lore widget.
- Multi-workspace isolation for content, search, audit, history, and writes.

### Roadmap And Active Flow Work

The repo roadmap names three broad milestones:

- Phase 1: stronger memory foundation for capture and consolidation.
- Phase 2: security hardening for standalone deployment.
- Beta release milestone: documented workflows, release checks, memory, and
  security in place.

Flow board snapshot for project `lore`:

- 78 total tasks.
- 69 done.
- 9 todo.

Active/planned Flow items that may affect UI design:

- Beta publish secrets and owner signoff.
- LLM-backed extraction with deterministic fallback.
- Extraction model eval and switch gate.
- Extraction dead-letter table and retry path.
- Mocked LLM provider test coverage.
- qwen3.6-plus extraction provider configuration.
- Provenance hardening in Search and RAG results: observed time, valid time,
  actor, lane, and source refs.
- Round-trip provenance test from capture to retrieval.
- CI lint, coverage, and dependency-audit gates.

Design implications from the Flow roadmap:

- Future search and RAG results should show more provenance context.
- There will likely be an extraction health/retry surface for failed LLM
  extraction batches.
- Model/eval status may need a compact admin/debug view.
- Patch-plan and consolidation review should become a real queue, not only an
  API concept.
- Graph exploration should anticipate more node types than pages.

## Terminology For Designers

- **Canonical page**: durable Markdown knowledge. This is the source of truth.
- **Capture**: draft observation from an agent, human, or automation. Not truth
  yet.
- **Promotion**: turning a capture into canonical page content.
- **Distillation**: combining captures into a daily note.
- **Consolidation**: extracting facts from captures and proposing safe page
  updates.
- **Patch plan**: proposed content change generated by consolidation.
- **Policy**: machine-readable rule that decides whether a patch can auto-apply
  or needs review.
- **Trace**: audit record explaining an important agent or automation decision.
- **Provenance**: where a claim came from and why it is trusted.
- **Epistemic status**: how knowledge was obtained, such as operator-declared,
  retrieved, inferred, or assumption.
- **Bi-temporal fields**: when a fact was observed versus when it is valid in
  the world.
- **Lane**: category or workflow lane for memory/context.
- **Precedent**: similar prior decision, trace, or case.

## Design Risks

- Do not make draft captures look like finished knowledge.
- Do not make graph nodes look equally trustworthy when some are canonical pages
  and others are drafts, claims, traces, policies, or missing links.
- Do not make raw API/MCP/debug links the main product navigation.
- Do not hide provenance entirely; trust is one of Lore's core reasons to
  exist.
- Do not design the graph as a standalone toy. It should connect back to pages,
  search, capture review, and provenance.
- Do not imply that RAG answers replace canonical pages. Retrieval is an access
  layer, not the source of truth.

## Source Notes

This brief was based on:

- `README.md`
- `docs/roadmap.md`
- `docs/api-reference.md`
- `docs/quickstart.md`
- `docs/agent-integration.md`
- `docs/consolidation.md`
- `docs/distillation.md`
- `docs/governance.md`
- `docs/policies.md`
- `docs/analytics-design.md`
- Existing templates in `lore_app/templates/`
- Flow project `lore`, including active tasks and completed idea/epic items as
  of 2026-06-01.
