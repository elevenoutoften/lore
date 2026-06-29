# Lore — System Review (agent + human surfaces)

_2026-06-22 · reviewed against live lore.axis.love (v0.3.0b1, api_key auth, LLM extractor configured) + the repo at HEAD 81561e4. Method: live agentic probe with a real `lore_` key, full read of routes/MCP/templates/SDK/docs, and a 23-agent adversarial review (7 surface maps → verify pass → 4-lens ergonomics panel)._

## TL;DR

- The **recent "huge update" is real and good**: Lore was reframed as agent-memory-first and got genuine depth — server-stamped tenancy, hybrid+dense recall with RRF, bi-temporal/contradiction-aware ranking, an automatic fact lifecycle (corroborate/supersede/decay), a LOCOMO-style eval gate in CI, MCP pagination + a `lore_overview` discovery tool, security hardening, and a reader-first wiki + graph.
- **But the headline promise is currently broken in production.** On an api_key instance with an LLM extractor (exactly what lore.axis.love runs), the documented "capture then recall just works" loop **returns 0** under default scoping. Confirmed live and root-caused. This silently undercuts the whole tenancy/recall epic. **This is the #1 thing to fix.**
- **Agent ergonomics: ~5/10.** The bones are excellent (discovery tool, self-diagnosing recall, explainable scores, safe-by-default destructive ops). The lived experience is dragged down by the broken core loop, surface sprawl (59 MCP tools + ~102 HTTP handlers, 5 overlapping "find" tools, dual capture endpoints), and a few hand-maintained lists that silently drift.
- **Human surface: thin but agent-ops-heavy.** Reading an article works well; the front door and nav are built for maintainers, not casual readers, and setup still implies pasting a token.

---

## 1. What the recent update changed (themes)

| Theme | What is now true | Key tasks |
|---|---|---|
| **Reframe** | Product is explicitly "agent memory backend first"; README/contract/UI rewritten; reader-first wiki + vis-network graph. | flow_000830, 832, 834 |
| **Tenancy / multi-actor** | Capture actor is **server-stamped** from the key (anti-spoof); recall/traces/provenance/precedent reads scoped to the caller actor; cross-actor needs admin + explicit flag. | flow_000843, 855, 873, 875, 878 |
| **Recall quality** | Hybrid retrieval fused with RRF, optional dense embeddings, bi-temporal + contradiction-aware ranking, multi-word FTS. | flow_000845, 847, 848 |
| **Trust / fact lifecycle** | Auto-consolidate on every capture; corroborate → auto-supersede → forget; content-idempotent patch apply; dead-letter retry. | flow_000839, 840, 842, 849, 857 |
| **Eval** | LOCOMO-shaped end-to-end hybrid+recall eval gates CI (recall@3, MRR, paraphrase/graph/contradiction/distractor expectations). | flow_000844, 871 |
| **MCP** | `has_more` pagination, `lore_overview` discovery tool, reader-role lockout fixed, body-aware `/mcp` write gate, safe-read browser cookie. | flow_000850, 856, 858, 879 |
| **Security / ops** | Admin-gated policy/audit/config; trusted-proxy hardened then made backward-compatible; ledger included in backup; opt-in maintenance scheduler; `/metrics`. | flow_000851, 852, 853, 880 |

**Tensions the review surfaced in this work:** the tenancy scoping (below) orphans recall; `pending_captures` reporting disagrees (30 vs 0) across surfaces; the maintenance scheduler is off by default; trace tenancy took several whack-a-mole rounds; a low-confidence backlog (84) is undrained.

---

## 2. The critical issue — capture→recall is broken on the production config

**Severity: HIGH (P0). Live-confirmed + root-caused, two compounding causes.**

What I observed live: captured a memory with the admin key → it auto-consolidated into 14 rich claims → `GET /api/memory/recall` returned **count 0**. `recall?cross_actor=true` returned them, stamped `actor: Nyx / Master / None` — never the caller. The `testing/nyx-agentic-smoke.py` script reproduces this deterministically (7/8 pass; only default-scope recall fails).

### Cause A — new captures: the LLM extractor discards the caller actor
- The capture page is correctly server-stamped (`frontmatter.actor = "dev"`, via `route_utils.stamp_capture_actor` / `actor_from_request`).
- The **deterministic** extractor inherits it: `lore_app/extraction.py:321` `actor = frontmatter.get("actor")` → claim actor = `dev`. ✓
- The **LLM** extractor does **not**: the prompt asks the model for a per-claim `actor` (`lore_app/llm_extractor.py:30`) and uses it verbatim — `actor=_clean_string(item.get("actor"))` (`llm_extractor.py:172`). The model infers actor from the capture *text* (my text said "Nyx" → actor `Nyx`; otherwise `None`). The authenticated caller is dropped.
- Result: **on any instance with an LLM provider (production), claims never carry the caller's actor.** Tests pass only because they use the deterministic extractor.

### Cause B — existing memory: tenancy scoping orphans `actor IS NULL` claims
- `recall_actor_scope` forces `actor = caller_actor` on the default path (`route_utils.py:144-146`); the ledger applies it as an exact-equality SQL clause (`ledger.py:1129-1131`), and ack does the same (`ledger.py:1282`).
- There is **no `OR actor IS NULL` fallback**, so every pre-tenancy / unattributed claim became unrecallable on upgrade. This violates the project's own rule: _"an upgrade must never break a working install."_

### Combined effect
On lore.axis.love (api_key + Ollama LLM): default recall returns ~nothing for normal single-agent use; only admin `cross_actor=true` works. The contract's headline "just works" (agent-memory-contract.md:71-84, worked example 122-147) is false for the recommended path, and the self-diagnosing hint doesn't detect the actor mismatch — so the feedback rail actively misleads.

### Fix (all backward-compatible)
1. **Bind claim actor to provenance, not the model.** In the LLM-result branch of `extraction.py` (~lines 113-121, which already backfills `observed_at`), re-stamp `claim.actor = capture.frontmatter actor` whenever set; ignore model-emitted actor. Mirrors the deterministic path.
2. **Make the scoped filter NULL-tolerant.** Change `ledger.py:1129-1131` (and the ack filter `:1282`) to `actor = ? OR actor IS NULL` on the non-cross_actor path. Keeps named-actor isolation; restores legacy memory on upgrade.
3. **Make the empty-recall hint actor-aware.** When a non-admin recall returns `count:0` but unscoped rows exist, say so instead of "no matching memory."
4. **Regression test on the recommended config:** api_key auth + LLM extractor (mocked to omit actor) → capture → consolidate → recall must be > 0. (Current eval uses the deterministic path, which is why CI is green.)
5. Stop advertising `actor` as a settable field on `lore_capture` (it's server-overridden) — see §4.

---

## 3. What agents have, and is it adequate?

**Surfaces:** HTTP (~102 handlers, 20 routers), MCP (59 tools at `POST /mcp`), Python SDK (`LoreClient` + `MemoryProvider`), TypeScript SDK, Hermes plugin.

**The core loop (capture → consolidate → recall → ack)** is well-designed on paper: auto-consolidation on by default, idempotent reads, explicit ack to boost salience, explainable `recall_signals`, and self-diagnosing `count:0` responses. `MemoryProvider` (Python) is the correct, complete client (capture/recall/ack/cross_actor, retries + circuit breaker).

**Adequacy gaps:**
- **The loop is broken on production config (§2).** Adequacy is gated on this.
- **SDK parity is uneven.** TypeScript SDK has **no durable-memory surface at all** (no `/api/memory/*`). The **Hermes plugin Nyx runs is capture-only** — 3 tools (`lore_search`, `lore_read`, `lore_capture`), writes via the **legacy `/api/capture`**, and exposes **no recall/ack**. So Nyx today gets half-open memory (write + page search), not the consolidated recall loop. `cross_actor` is reachable only from Python `MemoryProvider`.
- **`consolidation/run` is a no-op by default.** It defaults to `dry_run=true`, and even `dry_run=false` applies nothing because `max_auto_apply=0` (`schemas.py:1266`). An agent told "run consolidation" gets a silent no-op. (Safe, but surprising.)
- **`lore_overview` (the "call this first" index) lists only 40/59 tools and omits BOTH RAG tools** (`tools.py:1352-1380`) — agents that trust it never discover the strongest retrieval surface.
- **Reader/write gating rests on a hand-maintained denylist** (`WRITE_TOOL_NAMES`, `tools.py:61-82`; READ = all − WRITE). A new mutating tool forgotten there is silently reader-callable — a latent privilege bug. Only 3/59 tools carry MCP annotations; `prompts/list` is empty.
- **Retrieval overload:** ≥5 overlapping "find" tools (`lore_search`, `lore_rag_context`, `lore_rag_context_expanded`, `lore_recall`, `lore_context_graph`) with no decision rule, plus **two near-identical capture paths** (`/api/capture` draft vs `/api/memory/capture` ledger). `lore_capture` has 22 input properties incl. 5 overlapping provenance fields.

**Agent ergonomics panel (0–10):**

| Lens | Score | One-line |
|---|---|---|
| Cold-start discoverability | 6 | `lore_overview` + self-describing `/mcp` + self-diagnosing recall are strong; undercut by the silent actor trap, incomplete index, empty prompts. |
| Core-loop reliability | 5 | Excellent defaults, but the loop silently breaks on the recommended prod config. |
| Cognitive load & footguns | 4.5 | Surface sprawl, redundant retrieval tools, dual capture endpoints, server-overridden params advertised as settable. |
| Safety rails & feedback | 5 | Good 403s/role-gating/self-diagnosis, but the headline feedback rail misleads in the common config, and gating is a hand-maintained denylist. |

**Net:** the architecture is genuinely above-average for an agent memory backend; the _felt_ experience is ~5/10, almost entirely recoverable by fixing §2 and trimming the discovery/redundancy rough edges.

---

## 4. What humans have, and is it adequate?

**Surface:** `/` wiki + `/{page_id}` article reader (rendered Markdown, TOC, backlinks/outgoing/broken-link styling — this part is good), `/graph`, `/search`, `/api-keys`, `/settings`, plus agent-ops dashboards (`/captures`, `/procedures`, `/heartbeat`, `/lint`, `/rag`). Browser session = signed read-only cookie via `POST /api/login` (paste a `lore_` key) or Axis SSO trusted-proxy on the live deployment.

**Adequacy gaps (all low-severity UX, none are bugs):**
- **No "read an article" front door.** `index()` hard-codes `selected_page=None` (`pages.py:74`), so `/` always shows the welcome block whose CTAs are "Create an access key / Configure the model / Explore the graph" (`index.html:613-624`) — agent-onboarding, not reading. The only reading affordance is the sidebar list.
- **Nav is maintainer-heavy:** 5 of 9 links (Captures, Procedures, Health, Lint, RAG) are agent-ops dashboards a casual human doesn't need.
- **Setup implies pasting a token.** `/api-keys` and `/settings` need an admin bearer token in a paste box. On the live SSO deployment this _may_ be covered by the proxy role, but if not, a human pasting a `lore_` key after SSO login violates the owner's "minimum setup for humans" rule.
- **Agent surface leaks into the reader:** every article shows "Raw / Rendered JSON / Links JSON" links.

The model-key `/settings` page (provider/model/keys, masked secrets, hot reload) and the API-keys page are exactly the "simple web UI" the config-UX rule wants — good. The gap is purely the reader IA and the front door.

---

## 5. Doc accuracy (drift worth fixing)
- **Contract "just works" is false on the prod path** (§2) and the worked example never warns about actor scoping. (high)
- **Write rate limit:** docs say 30/60s; code default is **300**/60s (`config.py:109`). 10× stale. (low)
- **`lore_overview` and 2 other shipped tools missing from the README tool list.** (low)
- **`/api/config` and `/api/audit` are admin-gated in code but shown as plain endpoints with no-auth curl examples.** (low)
- **`quickstart.md` still uses the legacy `/api/capture` (title/observation) shape**, not `/api/memory/capture`. (low)
- **`configuration.md` documents ~19 of 48 `LORE_*` env vars** — omits `LORE_AUTO_CONSOLIDATE` (which the contract leans on). (low)
- **README front page doesn't link the docs hub / quickstart / api-reference / configuration / security.** (low)

---

## 6. Prioritized recommendations

**P0 — restore the core loop (small, backward-compatible):** §2 fixes 1–4.

**P1 — agent ergonomics:**
- Make `lore_overview` derive its taxonomy from `TOOLS` at runtime (never drift) + add a "which read tool?" decision table + echo the caller's resolved actor/role.
- Make MCP read/write classification derive from each tool's `readOnlyHint`/`destructiveHint`, assert in a test (fail-closed).
- Disambiguate the two capture paths; remove server-overridden `actor` from `lore_capture`'s schema; slim its provenance fields.
- Bring the Hermes plugin and TypeScript SDK up to the full `capture→recall→ack` loop (Nyx specifically needs `lore_recall`/`lore_ack`).
- Ship at least one MCP prompt encoding the loop incl. the actor caveat.

**P2 — human surface + docs:**
- Give `/` a real reader front door (featured/recent pages); split "Read" nav from "Operate" nav; hide raw-JSON links behind a toggle.
- Fix the doc drift in §5 (esp. the contract warning + the 30→300 rate-limit + quickstart capture shape).

---

## 7. Deliverables in this repo
- `testing/human-uat.md` — human surface UAT walkthrough.
- `testing/nyx-agentic-smoke.py` — runnable agentic loop test (reproduces §2).
- `testing/system-review.md` — this document.

_Note: live testing wrote a few clearly-marked test captures (`probe-…`, `nyx-smoke-…`) to lore.axis.love. They will consolidate/decay normally; say the word and I can prune them._
