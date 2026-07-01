# Lore UI/UX Data-Model Specification

## 1. Purpose & audience

This document is the **data contract** for the team designing Lore's human-facing wiki and knowledge-graph UI from scratch. It specifies exactly what data each screen can draw on — every field, its type, where it comes from, and which features that data can support — so the design team can lay out every screen without reading the backend code. It deliberately does **not** propose any visual design: layout, typography, color, iconography, and interaction patterns are entirely the design team's to own. Where this document says "supports a badge" or "supports an excerpt," read it as "the data exists to power that affordance if you choose to design one." Where it says data is **not** available, treat that as a hard constraint.

**On opinionatedness:** the field tables and "not available" notes are hard facts. Anything framed as an *option*, *implication*, or *precedent* — including §6, which collects how other memory systems (OpenPaw, mem0, KuzuDB) present these same surfaces — is a menu, not a mandate. The design team owns every visual and interaction choice.

---

## 2. Wiki — list & home surface

### 2.1 The list endpoint

`GET /api/pages` returns an array of **page summaries** (`PageSummary`). The response header `X-Total-Count` carries the total number of matching pages *before* paging — this is the number to drive any pagination/result-count UI.

**Filters** (all optional query params):

| Param | Type | Meaning |
|---|---|---|
| `kind` | string | One page kind (project, service, decision, runbook, procedure, concept, page, capture) |
| `visibility` | string | e.g. internal, public |
| `q` | string | Free-text filter over pages |
| `limit` | int 1–200 | Page size (omit = all) |
| `offset` | int ≥ 0 | Pagination offset |

To populate filter controls with **only the values that actually exist** in the corpus, call the catalog endpoint: `CatalogResponse` exposes `kinds: list[str]`, `visibilities: list[str]`, and `tags: list[str]`.

### 2.2 Fields on a list row (`PageSummary`)

| Field | Type | What it supports |
|---|---|---|
| `id` | string | Stable slug, path-like (`services/lore`). The link target. |
| `title` | string | Display title — the card/row heading. |
| `kind` | string | Drives a kind icon/badge. |
| `visibility` | string \| — | Drives a visibility badge. |
| `status` | string \| null | Lifecycle status (`stub`, `proposed`, `draft`…). Optional badge. |
| `summary` | string \| null | **One-line description — the single best field for a readable card subtitle/blurb.** |
| `tags` | list[str] | Tag chips. Defaults to `[]`. |
| `sources` | list[str] | Source references. Defaults to `[]`. |
| `source_task` | string \| null | Originating task id (`flow_000123`). Provenance. |
| `decision_id` | string \| null | Linked decision page. |
| `trace_id` | string \| null | Reasoning-trace correlation id. |
| `tool_calls` | list[dict] | Tool-call provenance records. Defaults to `[]`. |
| `constraints` | list[str] | Constraints that applied. Defaults to `[]`. |
| `policies_applied` | list[str] | Policy ids enforced. Defaults to `[]`. |
| `epistemic_status` | enum \| null | `operator_declared` / `retrieved` / `inferred` / `assumption` / `hearsay`. Trust badge. |
| `updated_at` | string (ISO) | Recency sort key. |
| `size` | int | Content size in bytes. |

**Not on a list row** (these require fetching detail — see §3): `created_at` (does not exist at all), `confidence`, `owner`, `reviewed_at`. Those live only in `frontmatter` on `PageDetail`.

### 2.3 Home sections — Featured vs Recently-updated

The home surface is built from the full page list with **captures removed first** (`kind == "capture"` are inbox/raw notes and never appear in the reader UI). Two curated sections come out of that filtered list:

| Section | Count | How it's selected |
|---|---|---|
| **Featured** | top **4** | Sorted by `(kind_rank, title, id)`. `kind_rank`: project=0, service=1, decision=2, runbook=3, procedure=4, concept=5, page=6, else=99. So it surfaces the **highest-value kinds first**, alphabetized within a kind. **Not** popularity- or link-count-based. |
| **Recently updated** | top **6** | Sorted by `updated_at` descending (then title, id). **Pure recency.** |

The two sections draw from the same list and **can overlap** (a page can be both Featured and Recent).

### 2.4 Readable card vs bare link — and how others do it

One data point from UAT: the current **Recently-updated list reads as noise** (a recency-ordered list of ids/timestamps with no narrative), while **Featured reads better**. The *why* is useful in data terms — both sections expose **identical fields**, so this is not a data limitation; it's a function of which fields a row actually *uses*. A row that surfaces only `id` + `updated_at` + `size` reads like a log line; a row that leads with `title` + `summary` + kind/status badges reads like content. `summary` is the load-bearing field for readability (and is nullable — design an empty/fallback state for stubs).

The treatment is therefore **open, not decided**. Approaches the data supports, with precedents:
- **Rich cards** — `title` + `summary` + `kind`/`status`/`tags` badges + optional `epistemic_status` trust signal. Works for any surface, recency or curated.
- **Faceted list/table** — mem0's dashboard presents memories as a filterable list (by user/agent/run); Lore's equivalent facets are `kind`, `visibility`, `lane`, `tags` (values from the catalog endpoint). A recency list stops being noise once it's filterable.
- **Lead with curation, de-emphasize recency** — surface Featured and treat recency as a minor "latest activity" strip, or drop it.

The hard facts here are only *which fields exist* (§2.2) and that both sections draw from the same data. Which treatment to use is the design team's call — see §6 for how other systems handle it.

---

## 3. Wiki — article view

An article is served by **two complementary endpoints**; a reader screen typically uses `/rendered`, while an edit/inspect affordance uses the raw one.

- `GET /api/pages/{id}` → `PageDetail` — raw markdown + parsed frontmatter (agent/edit oriented).
- `GET /api/pages/{id}/rendered` → `PageRendered` — safe HTML + table of contents + resolved links (reader oriented).

### 3.1 `PageDetail` (raw / inspect)

Inherits **all `PageSummary` fields** (§2.2), plus:

| Field | Type | Notes |
|---|---|---|
| `content` | string | Full raw markdown **including** the YAML frontmatter block. |
| `body` | string | Raw markdown body **without** frontmatter. |
| `frontmatter` | dict | Parsed metadata bag — the authoritative metadata source (§3.4). |

`content`/`body`/`frontmatter` are **agent-oriented / raw**. For a human reader they are the "View source / raw / JSON" affordance and can be hidden behind a secondary control.

### 3.2 `PageRendered` (the readable core)

Inherits all `PageSummary` fields (but **not** `content`/`body`/`frontmatter`), plus:

| Field | Type | What it supports |
|---|---|---|
| `html` | string | **Sanitized HTML body — the main readable article.** Safe to inject directly. |
| `toc` | list[`TocEntry`] | Table of contents. Defaults `[]`. |
| `links` | list[`RenderedLink`] | All outgoing links (internal + external). Defaults `[]`. |
| `missing_links` | list[`RenderedLink`] | Internal links whose target page doesn't exist (broken wiki-links). Defaults `[]`. |

**`TocEntry`**: `level: int` (only h1–h3 are included; deeper headings are skipped), `id: str` (slug anchor), `title: str`. Anchors are unique — duplicates get `-2`, `-3` suffixes. → Supports a sidebar/inline TOC with up to 3 nesting levels.

**`RenderedLink`**: `href: str` (rewritten, e.g. `/services/lore#section`), `label: str | null`, `page_id: str | null` (resolved internal id; null = external), `exists: bool`, `external: bool`. Missing internal links carry CSS class `wiki-link wiki-link--missing` and a `title` of "Lore page not found: {id}" inside the HTML. → Supports distinct styling for internal vs external vs broken links.

**Rendering behavior baked into `html`** (the design team inherits these — no need to re-implement):
- CommonMark + tables + strikethrough + linkify. Raw HTML input is disabled; `<script>/<style>/<iframe>/<object>/<embed>` are stripped; output is allowlist-sanitized. **`html` is safe to inject.**
- A leading `# Title` matching the page title is **stripped** — the UI chrome owns the title; don't expect an H1 in `html`.
- `[[wiki-links]]` (`[[label|target]]` or `[[target]]`) are resolved into anchors.
- Headings get an `id` and a `#` anchor link (`<a class="heading-anchor">`).
- Tables are wrapped in `<div class="table-scroll">` — design horizontal-scroll behavior for wide tables.
- Inline links/images allowed only for http/https/mailto/tel.

### 3.3 Links, history (side panels)

**Links** come from a dedicated structure, `PageLinks` (not a field on the page model):

| Field | Type | Meaning |
|---|---|---|
| `page` | `PageSummary` | The subject page. |
| `outgoing` | list[`LinkEdge`] | Links this page points to. |
| `backlinks` | list[`LinkEdge`] | Pages that link **to** this page. |
| `missing_links` | list[`LinkEdge`] | This page's broken outgoing links. |

`LinkEdge`: `source`, `source_title`, `target` (null if unresolved), `target_title`, `href`, `label`, `exists: bool`, `external: bool`, `relationship_type` (default `"wikilink"`). Backlinks can be grouped for a sidebar. → Supports "Linked from" (backlinks), "Links to" (outgoing), and a "broken links" warning panel.

**History**: `GET /api/pages/{id}/history` → a list of audit entries (create / update / metadata_update / stub_create / delete) with a summary and diff-size — these are **operation records, not full content revisions**. → Supports an "Activity / history" panel, **not** a diff/restore-prior-version viewer. Because there is **no `created_at`** anywhere on the model, the **earliest history entry is the proxy for "created."**

### 3.4 Frontmatter — per-kind metadata (`frontmatter` on `PageDetail`)

`frontmatter` is a free dict, but `FrontmatterSpecResponse` (`specs` per kind + `all_fields`) defines required/optional fields per kind — use it for field labels and required-ness. Union of fields a reader UI may encounter:

| Field | Use |
|---|---|
| `title`, `kind`, `visibility` | Required on all kinds. |
| `summary` | One-line summary (required project/service/decision/runbook/concept/procedure). |
| `owner` | Owning person/team (required project/service). **Frontmatter only.** |
| `tags`, `sources` | Tags; citations. |
| `status` | Lifecycle status. |
| `stale_after` | Date after which the page is stale → drives a **"stale" warning**. |
| `reviewed_at` | Last human-review date. **Frontmatter only.** |
| `confidence` | low/medium/high/unknown → trust badge. **Frontmatter only.** |
| `epistemic_status` | How the knowledge was obtained (enum). |
| `decided_at`, `deciders`, `alternatives` | Decision kind (first two required). |
| `dependencies` | Service kind. |
| `steps` | Runbook / procedure (ordered). |
| `trigger`, `preconditions`, `postconditions`, `error_handling` | Procedure kind (`trigger` required). |
| `schema_version`, `validated`, `validated_at`, `author` | Procedure metadata (required for procedure). |
| `captured_at`, `agent`, `source_task`, `suggested_target_page`, `related`, `evidence`, `promoted_to` | Capture-kind provenance — **captures aren't in the reader UI**, so a reader screen never renders these. |

### 3.5 Reader-facing vs agent-oriented (what to hide)

| Reader needs (surface prominently) | Agent-oriented / raw (hide behind "details/source") |
|---|---|
| `title`, `html`, `toc`, `summary` | `content`, `body`, `frontmatter` (raw dict) |
| `kind`, `visibility`, `status` badges | `trace_id`, `tool_calls`, `constraints`, `policies_applied` |
| `tags`, `owner` | `source_task`, `decision_id` (raw ids) |
| `links` / `backlinks` / `missing_links` | The two raw endpoints / "View JSON" |
| Trust signals: `epistemic_status`, `confidence`, `reviewed_at`, `stale_after` | `size`, `schema_version`, `validated*` |
| History as an activity panel | History raw audit dataclass fields |

**Provenance, for a "where did this come from" affordance**, comes from: `epistemic_status` (summary + frontmatter); `confidence` / `reviewed_at` / `stale_after` / `owner` (frontmatter); and `sources`, `source_task`, `decision_id`, `trace_id`, `tool_calls`, `constraints`, `policies_applied` (summary/detail). This is rich enough to design a full provenance disclosure — but it reads as machine metadata, so treat it as progressive disclosure, not primary chrome.

---

## 4. Search

`SearchResponse` = `{ query: str, hits: list[SearchHit] }`.

### 4.1 Fields per result (`SearchHit`)

| Field | Type | What it supports |
|---|---|---|
| `page` | `PageSummary` | Full summary — title, kind, visibility, tags, **summary**, updated_at, etc. (all §2.2 fields). |
| `score` | int | Integer relevance. Sort order / relevance indicator. |
| `matches` | list[str] | Matched terms/fragments. Defaults `[]`. The closest thing to a snippet — see below. |
| `observed_at` | string \| null | When the fact was observed. |
| `valid_from` | string \| null | When the fact became true. |
| `valid_until` | string \| null | When it ceased to be true (null = still valid). |
| `actor` | string \| null | Agent that created the content. |
| `lane` | string \| null | Retrieval lane (project / procedural / ops / companion / draft). Lane badge. |
| `source_refs` | list[str] | Source page/capture ids referenced. |

### 4.2 EXPLICIT: is there a usable excerpt/snippet?

**No.** `SearchHit` has **no dedicated snippet / excerpt / highlighted-body field.** What you have for a result row is:

- `page.title` — the heading.
- `page.summary` — the **best available one-line blurb; use it as the row subtitle.** (Nullable — design an empty state.)
- `matches: list[str]` — a list of **matched term fragments**, not a contiguous sentence. Usable as **highlighted keyword chips**, *not* as a contextual sentence-level excerpt.

To show a real text excerpt you would have to separately fetch `PageDetail` and excerpt `body` yourself; the search response does not contain one. So a search row can be designed to show: **title, summary (subtitle), kind badge, lane badge, tags, score, and matched-term chips** — and that is the full extent of what the data supports without an extra fetch.

### 4.3 What the data can support for a live, in-place search UX

The current search shows no excerpt and has no live-search/back behavior. What the data *can* support:

- **Live/instant results** — `SearchResponse` is a flat list of self-describing hits (each carries its full `page` summary), so an as-you-type search can render rows immediately from one call, with no detail fetch required for a basic row.
- **In-place result rows** — every hit is independently renderable (title + summary + badges + matched chips + score), so results can render in a dropdown/panel without navigating away. There is no server-side cursor/back state; the client owns query and result state.
- **Faceting client-side** — because each hit embeds `kind`, `visibility`, `tags`, and `lane`, results can be grouped or filtered in the UI without re-querying.
- **Temporal/provenance context per hit** — `observed_at`, `valid_from`, `valid_until`, `actor`, `lane` let a row optionally show recency/validity/agent context.
- **What it cannot do without extra work:** show a contextual sentence excerpt around the match (requires a `PageDetail` fetch + client-side excerpting). If excerpts are a design requirement, budget a second request per expanded/hovered row.

> Note: richer multi-hop retrieval (`RagExpandedResult`) adds citations, relevance paths, "relevant because," supporting/contradicting claims, etc., but the plain `/search` `SearchHit` above is what feeds a standard results list.

---

## 5. Graph — nodes, edges, preview, analytics

Lore has **two graphs**. Design for the richer one.

| Graph | What it is | API |
|---|---|---|
| **Link Graph** ("enriched") | Page-to-page wikilinks + source refs. Pages only. | `/api/links`, `/api/graph/*` |
| **Context Graph** | Full multi-type knowledge graph: pages, captures, claims, traces, plans, policies, actors, tools, tasks, sources. The lore2 SPA visualizes a **page-scoped slice** of it (`node_types=page`) as the knowledge map; the full typed graph is API/MCP-queryable but not drawn. | `/api/context-graph*`, `/api/graph/analytics` |

**What the lore2 SPA renders today:** the knowledge map is the **Context Graph fetched page-only** — `GET /api/context-graph?node_types=page&limit=1500` — drawn as a canvas force layout of page nodes plus their page-to-page edges (`mentions` wikilinks + `provenance`). The legend filters by page **kind** (projects/services/decisions/runbooks/procedures/concepts/pages); hover highlights a node's neighborhood, click opens the page reader. It requests the page-only slice deliberately: the map shows curated pages, so the server scopes the payload rather than shipping every claim/plan/trace/policy for the client to drop.

**The richer option (not yet built):** the **Context Graph** is the full knowledge model (12 node types); the map above is a page-only projection of it. Visualizing the *typed* graph — claims, traces, plans, policies, actors, tools as first-class nodes with per-type selection panels — remains open. Other systems split here too: OpenPaw and KuzuDB visualize a typed graph, while mem0 ships no graph at all. If Lore grows the map into the typed graph, that is the more capable target; how heavy to make it is a design call (see §6.4).

### 5.1 Nodes

**Link-graph node (`GraphNode`)** — what's rendered today:

| Field | Type | Meaning |
|---|---|---|
| `page_id` | str | Page id (path-like). |
| `title` | str | Page title. |
| `kind` | str | page / capture / concept / decision / project / procedure / runbook / service… |
| `visibility` | str | public / internal / private. |
| `tags` | list[str] | Tags. |
| `summary` | str \| null | Used in hover tooltip. |
| `inbound_count` | int | Existing internal inbound wikilinks (degree-in). |
| `outbound_count` | int | Existing internal outbound wikilinks (degree-out). |

Degree counts only `exists && !external` wikilinks; `degree = inbound + outbound` is computed client-side (drives node sizing).

**Context-graph node (`ContextGraphNode`)** — the rich model:

| Field | Type | Meaning |
|---|---|---|
| `id` | str | Globally unique, namespaced by type (see ID conventions). |
| `type` | enum | One of 12 node types (below). |
| `label` | str | Display label (page title, claim text, actor name…). |
| `metadata` | dict | **Type-dependent bag** — empty/null/`[]`/`{}` values are dropped. All the interesting attributes live here. |

**There is no top-level `degree`, `salience`, or `centrality` on a node** — those come from analytics (§5.4), keyed by node id, and merge client-side.

**Node types & id conventions:**

| `type` | Produced from | ID convention |
|---|---|---|
| `page` | Lore page (kind ≠ capture) | raw page id |
| `capture` | Lore page kind=capture / referenced capture | raw capture id |
| `entity` | extraction candidate (entity); fallback for unknown candidate | `candidate:<id>` |
| `claim` | ledger candidate (claim; candidate/active only) | `candidate:<id>` |
| `invalidation` | ledger candidate (invalidation) | `candidate:<id>` |
| `plan` | patch plan (proposed page edit) | `plan:<plan_id>` |
| `trace` | reasoning trace | `trace:<trace_id>` |
| `actor` | agent that authored a page/claim/trace | `actor:<name>` |
| `task` | Flow/external task ref | `task:<task_id>` |
| `policy` | governance policy | `policy:<policy_id>` |
| `source` | file path or URL cited | `source:<path-or-url>` |
| `tool` | tool-call record | `tool:<label>:<12-char sha1>` |

**`metadata` keys by type** (what a hover/select panel can show per type):

- **page / capture**: `kind`, `visibility`, `status`, `tags`, `observed_at`, `placeholder` (true if materialized only as an edge endpoint, no real page loaded).
- **claim / invalidation / entity**: `batch_id`, `candidate_type`, `status` (candidate/active), `confidence`, `strength`, `lane` (project/procedural/ops/companion/draft), `actor`, `observed_at`, `valid_from`, `valid_until` (**bi-temporal**: world-truth start/end; `valid_until` null = still valid). Claim `label` is `"subject predicate object"` (fallbacks: name → `new_fact` → candidate id).
- **plan**: `batch_id`, `operation`, `risk_level`, `auto_appliable` (bool), `status`.
- **trace**: `actor`, `status`, `reason_summary` (≤100 chars).
- **policy**: `gate`, `version`, `enabled`, `condition_kind`, `condition_operation`.
- **source**: `path` or `url`. **tool**: full tool-call dict + `referenced`. **placeholders** (task/trace/policy/entity added only as edge targets): `referenced: true`.

Graph-level `stats` (`ContextGraph.stats`): `dict[str,int]` — one count per node type plus `"edges": <count>`.

### 5.2 Edges

**Link-graph edge (`LinkEdge`)**: `source`, `source_title`, `target` (null if unresolved), `target_title`, `href`, `label`, `exists: bool`, `external: bool`, `relationship_type` (`"wikilink"` | `"source_ref"`). `broken_links` = `!external && !exists`. Today's UI only draws `wikilink && exists && !external`.

**Context-graph edge (`ContextGraphEdge`)** — directed, deduped on `(source, target, type, label, metadata)`:

| Field | Type | Meaning |
|---|---|---|
| `source` | str | Source node id. |
| `target` | str | Target node id. |
| `type` | enum | Edge type (13 values; **kebab-case on the wire**). |
| `label` | str | Short label (referenced id, wikilink text, tool label…); default `""`. |
| `metadata` | dict | Compacted extras (usually empty today). |

**Edge types** (note kebab-case wire values):

| Wire value | Direction (source → target) | Emitted when |
|---|---|---|
| `provenance` | page/trace → page/capture/candidate/trace/policy | related_pages, capture_ids, candidate provenance, trace context_refs |
| `mentions` | page → wikilink target | inline `[[wikilink]]` in body |
| `supports` | claim/entity → page | candidate `source_page_ids` (non-invalidation) |
| `contradicts` | invalidation → page | invalidation candidate `source_page_ids` |
| `supersedes` | newer claim → older claim | candidate supersedes/superseded_by |
| `generated` | capture → candidate; trace → plan | capture produced candidate; trace produced plan |
| `applied` | plan → page / plan → candidate | plan targets a page / applies candidates |
| `used-policy` | page/plan/trace → policy | policies_applied / policy_refs |
| `source-of` | source → page/trace | a file/URL source cited |
| `task-related` | page/trace → task | task_id / decision_id / provenance task_ids |
| `parent` | parent trace → child trace | trace parent_trace_id |
| `authored` | actor → page/claim/trace | `actor` present |
| `used-tool` | page/trace → tool | tool_calls / tool_refs |

**No separate "backlink" or "derived" edge type.** Backlinks are derived by **reversing edges**. "Derived" semantics are carried by `generated` / `applied` / `supersedes`.

### 5.3 Preview on hover / select (no page load required)

- **lore2 map node**, today: the pages-only knowledge map opens the page reader on click and highlights the hovered node's neighborhood; labels show by degree (Smart) or all/off. A hover/select preview panel per node is still available to design from the fields below.
- **Richer preview without fetching the page** is available from data already in hand:
  - Enriched node fields: `title`, `kind`, `visibility`, `tags`, `summary`, `inbound_count`, `outbound_count`.
  - Context-graph node `metadata` (type-dependent): status, lane, actor, confidence, strength, `valid_from`/`valid_until`, `observed_at`, risk_level, reason_summary, policy gate/version, etc.
  - **`POST /api/context-graph/explain`** → node + expanded neighborhood + a human-readable `explanation` string summarizing edge-type counts. **Ideal as a "selected node" side panel** without loading the page.
  - **`GET /api/pages/{page_id}/links`** → `PageLinks` (outgoing / backlinks / missing_links) for a link-focused preview.

**Design implication:** because attributes are type-dependent, design **per-node-type hover/select panels** (a claim panel shows confidence/strength/validity; a policy panel shows gate/version/enabled; a trace panel shows reason_summary; etc.).

### 5.4 Analytics — `GET /api/graph/analytics`

Computed on the **context graph** (scoped by actor); advisory, never canonical. Response `GraphAnalyticsResult`:

| Field | Type | Meaning |
|---|---|---|
| `node_metrics` | dict[node_id → `GraphMetrics`] | Per-node metrics. |
| `communities` | list[list[str]] | Clusters (label-propagation, ≤50 iters); each a sorted list of node ids. |
| `top_nodes` | dict | `{"degree_centrality": [top 10 ids], "betweenness": [top 10 ids]}`. |
| `computed_at` | str (ISO) | Timestamp. |
| `node_count` / `edge_count` | int | Graph size. |

`GraphMetrics` per node: `node_id`; `degree_centrality` (normalized 0–1, undirected projection); `betweenness_centrality` (Brandes, normalized; **sampled** at ≥50 nodes — sources capped at 20, then rescaled); `community` (integer index into `communities`).

**For UI:** **counts** come from `stats` / `node_count` / `edge_count`; **centrality** (degree + betweenness) and **community/cluster** membership come from this endpoint, keyed by `node_id` so they merge onto `/api/context-graph` nodes client-side (drive node size, color-by-community, "important nodes" lists). There's an internal `semantic_entry_points(k)` bridge-score helper that is **computed but not exposed via any route** — don't design around it.

### 5.5 Query surface (params & limits)

**Link-graph endpoints:**

| Endpoint | Returns |
|---|---|
| `GET /api/links` | `LinkGraphResponse` (pages + links + broken_links) |
| `GET /api/graph/stats` | `{pages, links, broken_links}` counts |
| `GET /api/graph/enriched` | `EnrichedLinkGraphResponse` (nodes + links + broken_links) — current UI |
| `GET /api/graph/sources` | `list[LinkEdge]` (source_ref edges) |
| `GET /api/pages/{page_id}/links` | `PageLinks` |
| `GET /graph` | HTML page (vis-network) |

**Context-graph endpoints** (prefix `/api/context-graph`). All accept `actor` (optional) and `cross_actor` (bool, default false). **Actor scoping is enforced server-side:** under auth the actor is forced to the caller's actor unless `cross_actor=true` **and** caller role is `admin` (else 403). Scoping drops other actors' `actor` nodes and any node whose `metadata.actor` ≠ scope.

| Endpoint | Params | Defaults | Limits |
|---|---|---|---|
| `GET /api/context-graph` | — (whole graph) | — | — |
| `POST /api/context-graph/neighbors` | `node_id` (req); `direction` ∈ outgoing/incoming/both; `edge_types[]`; `node_types[]`; `limit` | direction=both, limit=50 | limit 1–500 |
| `POST /api/context-graph/paths` | `source_id` (req); `target_id` (req); `max_depth`; `edge_types[]`; `limit` | max_depth=3, limit=10 | max_depth 1–6, limit 1–50 |
| `POST /api/context-graph/explain` | `node_id` (req); `depth`; `edge_types[]` | depth=2 | depth 1–3 |

**Response shapes:** neighbors → `{ node_id, neighbors: [{node, edge}], total }` (`total` = count *before* limit). paths → `{ source_id, target_id, paths: [{source_id, target_id, steps:[{edge, node}], length}] }` (BFS over **outgoing** edges only; distinct paths deduped). explain → `{ node, neighborhood:[{node, edge}], explanation }`.

**Not wired to HTTP** (internal pure functions — don't design around them as live endpoints): `ego_subgraph(center_id, radius≥1)`, `neighbors_of(node_id, depth)` (undirected BFS). The current HTML UI builds its **own** client-side ego view (undirected wikilink BFS, depth slider 1–3) from the enriched payload.

---

## 6. Design precedents from other memory systems

**References, not requirements** — how comparable systems present the same surfaces, so the design team has a menu of proven approaches rather than this document's opinions. Drawn from a source-level comparison of OpenPaw, mem0, KuzuDB/KuzuMemory, and Honcho (licenses vary — Apache-2.0, AGPL-3.0, and MIT-with-no-committed-LICENSE-file — these are design references, not code to copy).

### 6.1 List / memory surface
- **mem0** — a Next.js dashboard presenting memories as a **filterable list/table** with metadata, faceted by `user / agent / run`. No graph. Closest precedent for a faceted Lore page browser (Lore facets: `kind`, `visibility`, `lane`, `tags`).
- **KuzuMemory** — no browser list at all; memory surfaces only as MCP tool output inside the IDE. A precedent for a "no human list" stance if the audience is purely agents.
- **Honcho** — **no human UI at all**: REST API + Python/TS SDKs only (its hosted dashboard is billing-only). A SOTA system that ships zero reader/list surface — a strong data point for keeping the human UI minimal.
- **Lore today** — page browser + sidebar list.

### 6.2 Article / detail surface
- **OpenPaw** — a **node detail panel** (properties, aliases, tags) on selection. Precedent for Lore's per-page/per-node detail and the provenance disclosure (§3.5).
- **mem0** — a flat memory entry with metadata; no rich article view.
- **Lore** is unusually rich here (rendered Markdown + TOC + backlinks + provenance) — closest to a wiki, with no competitor analog. This surface is more greenfield than borrowed.

### 6.3 Search
- None of the three ship a notable end-user search UX: **mem0** is filter-driven (not query-driven), **KuzuMemory** is regex/MCP, **OpenPaw** is graph-first. Lore's search redesign has **few precedents to borrow** — design it from the data (§4.3: live, in-place, faceted; no excerpt field).

### 6.4 Graph visualization
- **OpenPaw** (`web/graph.html`) — interactive node graph with **click-to-detail** and **type filtering** (person / project / place / org / concept). The closest proven model for Lore's context graph (12 node types) with per-type filters + a selection panel.
- **KuzuDB** — graph-DB browser UIs (Vela browser; the **Bighorn** fork) and React hooks in the TS port; a heavier, Cypher-results-on-a-canvas / query-driven explorer.
- **mem0** — no graph viz at all (entity list only) — a reminder that a graph UI is optional, not mandatory.
- **Honcho** — no graph visualization either (peer-collection model, not a graph engine).
- **Lore today** — the lore2 SPA renders a pages-only knowledge map (the Context Graph fetched `node_types=page`) as a canvas force layout with kind-filter legend + click-to-read (§5). The next step toward the richer *typed* context graph is OpenPaw's click-to-detail + per-type filter.

### 6.5 Distinct to Lore (no precedent to copy)
Lore's **capture-review queue**, **knowledge-quality lint dashboard**, and **policy/governance** views have no analog in the other systems — they're agent-ops, not reader surfaces. The owner wants the human UI minimal, so treat these as a separate "operate" console, kept out of the reader redesign.

### 6.6 The minimal-/no-UI stance
The owner's goal is a *minimal* human UI, and the field strongly supports that: **Honcho** (SOTA) ships **no human UI** (API/SDK/MCP only), **mem0's** is an admin/management dashboard, **KuzuMemory** has none. The implication: Lore's human surface can be just the **reader wiki + graph** (the genuinely human-facing parts), with capture/lint/policy/RAG either demoted to a separate agent-ops console or dropped from the human UI entirely. Honcho also *replaces* a UI with a **prompt-ready context API** (`session.context().to_openai()` / `.to_anthropic()`) and a natural-language **chat** endpoint — i.e. the way a human or agent "reads" memory can be an API that returns formatted context, not a screen. That's a backend idea, but it directly informs how thin the UI can be.

---

## 7. Sample payload shapes

Schematic — field names from code, values illustrative. Optional/nullable fields may be absent or `null`.

**Page summary** (`PageSummary` — a list row / search `page`):
```json
{
  "id": "services/lore",
  "title": "Lore",
  "kind": "service",
  "visibility": "internal",
  "status": "draft",
  "summary": "Canonical knowledge base for the Axis projects.",
  "tags": ["knowledge", "axis"],
  "sources": ["https://example.com/spec"],
  "source_task": "flow_000123",
  "decision_id": null,
  "trace_id": "trace_abc123",
  "tool_calls": [],
  "constraints": [],
  "policies_applied": ["policy_pii_block"],
  "epistemic_status": "operator_declared",
  "updated_at": "2026-06-20T14:03:00Z",
  "size": 4821
}
```

**Rendered article** (`PageRendered` — summary fields + these; `content`/`body`/`frontmatter` are NOT here):
```json
{
  "id": "services/lore",
  "title": "Lore",
  "kind": "service",
  "visibility": "internal",
  "summary": "Canonical knowledge base for the Axis projects.",
  "tags": ["knowledge", "axis"],
  "updated_at": "2026-06-20T14:03:00Z",
  "html": "<p>Lore stores canonical facts…</p><h2 id=\"architecture\">Architecture <a class=\"heading-anchor\" href=\"#architecture\">#</a></h2>…",
  "toc": [
    { "level": 2, "id": "architecture", "title": "Architecture" },
    { "level": 3, "id": "storage", "title": "Storage" }
  ],
  "links": [
    { "href": "/services/flow", "label": "Flow", "page_id": "services/flow", "exists": true, "external": false },
    { "href": "https://example.com", "label": "spec", "page_id": null, "exists": true, "external": true }
  ],
  "missing_links": [
    { "href": "/services/ghost", "label": "Ghost", "page_id": "services/ghost", "exists": false, "external": false }
  ]
}
```

**Raw detail extras** (`PageDetail` adds these to the summary fields):
```json
{
  "content": "---\ntitle: Lore\nkind: service\n…\n---\n# Lore\n\nLore stores…",
  "body": "Lore stores canonical facts…",
  "frontmatter": {
    "title": "Lore", "kind": "service", "visibility": "internal",
    "owner": "platform-team", "confidence": "high",
    "reviewed_at": "2026-06-10", "stale_after": "2026-12-31",
    "epistemic_status": "operator_declared", "tags": ["knowledge", "axis"]
  }
}
```

**Search hit** (`SearchHit`):
```json
{
  "page": { "...": "a full PageSummary object (see above)" },
  "score": 87,
  "matches": ["knowledge base", "canonical"],
  "observed_at": "2026-06-18T09:00:00Z",
  "valid_from": "2026-06-01T00:00:00Z",
  "valid_until": null,
  "actor": "nyx",
  "lane": "project",
  "source_refs": ["captures/2026-06-18-intake"]
}
```

**Context-graph node** (`ContextGraphNode` — `claim` example; `metadata` keys vary by `type`):
```json
{
  "id": "candidate:clm_00917",
  "type": "claim",
  "label": "Lore stores canonical facts",
  "metadata": {
    "batch_id": "batch_204",
    "candidate_type": "claim",
    "status": "active",
    "confidence": "high",
    "strength": "strong",
    "lane": "project",
    "actor": "nyx",
    "observed_at": "2026-06-18T09:00:00Z",
    "valid_from": "2026-06-01T00:00:00Z",
    "valid_until": null
  }
}
```

**Context-graph node** (`page` example):
```json
{
  "id": "services/lore",
  "type": "page",
  "label": "Lore",
  "metadata": {
    "kind": "service",
    "visibility": "internal",
    "status": "draft",
    "tags": ["knowledge", "axis"],
    "observed_at": "2026-06-20T14:03:00Z"
  }
}
```

**Context-graph edge** (`ContextGraphEdge` — note kebab-case `type`):
```json
{
  "source": "candidate:clm_00917",
  "target": "services/lore",
  "type": "supports",
  "label": "clm_00917",
  "metadata": {}
}
```

**Link-graph node** (`GraphNode` — what's rendered today) & **edge** (`LinkEdge`):
```json
{
  "page_id": "services/lore",
  "title": "Lore",
  "kind": "service",
  "visibility": "internal",
  "tags": ["knowledge", "axis"],
  "summary": "Canonical knowledge base for the Axis projects.",
  "inbound_count": 5,
  "outbound_count": 2
}
```
```json
{
  "source": "services/flow",
  "source_title": "Flow",
  "target": "services/lore",
  "target_title": "Lore",
  "href": "/services/lore",
  "label": "Lore",
  "exists": true,
  "external": false,
  "relationship_type": "wikilink"
}
```

**Graph analytics** (`GraphAnalyticsResult`, abridged):
```json
{
  "node_metrics": {
    "services/lore": { "node_id": "services/lore", "degree_centrality": 0.42, "betweenness_centrality": 0.18, "community": 0 }
  },
  "communities": [ ["services/lore", "services/flow"], ["candidate:clm_00917"] ],
  "top_nodes": { "degree_centrality": ["services/lore", "…top 10 ids"], "betweenness": ["services/lore", "…top 10 ids"] },
  "computed_at": "2026-06-25T00:00:00Z",
  "node_count": 460,
  "edge_count": 1203
}
```

---

**Key constraints the design team must honor:**
- **Captures never appear in the reader UI** — filter `kind == "capture"` out of every reader/list/home surface.
- **There is no `created_at`** — use the earliest history entry as the "created" proxy.
- **Search has no excerpt/snippet field** — `summary` + `matches` (keyword chips) only; a true contextual excerpt needs a second `PageDetail` fetch.
- **`html` is pre-sanitized and safe to inject; its title H1 is already stripped** — the UI chrome owns the title.
- **Edge type values are kebab-case on the wire** (`used-policy`, `source-of`, `task-related`, `used-tool`).
- **Centrality/communities/degree are a separate analytics call keyed by `node_id`** — merge client-side; nodes carry no top-level centrality.
- **Backlinks and "derived" are not edge types** — reverse edges for backlinks; `generated`/`applied`/`supersedes` express derivation.
- **Actor scoping is server-enforced** on the context graph; cross-actor views require admin + explicit `cross_actor=true`.
