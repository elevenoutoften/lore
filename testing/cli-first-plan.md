# Lore — agent-first interface plan (CLI primary, slim MCP, one client)

_Plan for the tooling direction agreed 2026-06-22. Goal: an agent (Hermes, Codex,
Claude) installs Lore and uses it out of the box like any memory backend — the
six-verb loop on a token-cheap CLI, with MCP demoted to a thin option._

## Principle

One memory client, exposed as **six verbs**, identical across every transport:

```
capture   recall   ack   search   read   write
```

```
                     HTTP API   (wire protocol — unchanged)
                          │
                 one memory client   (sdk/python: MemoryProvider is ~80% of it)
        ┌─────────────────┼──────────────────────────┐
   lore CLI            Hermes plugin             slim MCP
  (+ skill, primary)   (native install)      (core 6 by default)
```

- **CLI is the primary agent interface** — shell-invoked, terse text out, 0 tokens
  of upfront schema. (MCP `tools/list` is ~7.5k tokens loaded every session today.)
- **MCP stays but is demoted and slimmed** — default surface = the 6 core tools;
  the ~53 power/maintenance tools move behind one discovery tool or out of MCP.
- **Same six verbs, same semantics everywhere.** Match the `mem.capture()/recall()`
  model agents already have.

## 1. The agent CLI (L-CLI-01) — the keystone

A lightweight **HTTP-client** CLI that wraps the canonical client and talks to a
remote Lore with a bearer key. Lives in `sdk/python` (stdlib-only, no server
install needed), installable via pip **and** bundled in the skill.

| Command | Endpoint | Output (default text; `--json` for structured) |
|---|---|---|
| `lore capture <text> [--lane --source-task --confidence]` | POST /api/memory/capture | `captured <capture_id>` |
| `lore recall <query> [--limit --lane --subject --cross-actor]` | GET /api/memory/recall | one line per claim: `score  subject predicate object  [candidate_id]` + the hint on count 0 |
| `lore ack <candidate_id>...` | POST /api/memory/recall/ack | `acked N` |
| `lore search <query> [--limit --kind]` | GET /api/search | one line per page hit |
| `lore read <page_id>` | GET /api/pages/{id} | raw Markdown |
| `lore write <page_id> [--file \| --stdin]` | PUT /api/pages/{id} | `wrote <page_id>` |
| `lore whoami` | (derive from a recall probe / config) | resolved actor + role + base_url — surfaces the actor scope so the capture→recall identity is never a mystery |

- Config: `LORE_BASE_URL` (default `https://lore.axis.love`), `LORE_API_KEY`. No flags required for the happy path.
- Built on the SDK `MemoryProvider` (capture/recall/ack already exist there) + `LoreClient` (search/read/write). Retries + circuit breaker come for free.
- Exit codes + `--json` so it composes in scripts and other agents' tool layers.

**Open decision (needs your call):** the server already ships a `lore` console
script (operator-only: backup/consolidate/key). The agent CLI wants the same name.
They are different install targets (agent host has only the SDK; server host has
the full package), so the collision is usually harmless — but the clean options are:
(a) keep both `lore`, rely on install target; (b) rename the **server** CLI to
`lore-admin` and give the agent CLI `lore`; (c) ship the agent CLI as `lore` from
the SDK and `python -m lore_sdk` as the fallback. Recommendation: **(b)** — `lore`
is the thing agents type; the operator tool is the rarer, host-local one.

## 2. The skill (L-SKILL-01) — "install via link"

The lore repo ships its own `skill/` (SKILL.md + the bundled CLI client) so
"install via link" pulls a current skill. It supersedes the hand-maintained
`~/.claude/skills/lore` (which is stale: claims "Lore reuses Flow's API keys" — false —
and never mentions the memory loop).

- SKILL.md leads with the **six-verb loop and the CLI**, not a wall of endpoints.
- ~1 screen: when to use, the 6 commands with one example each, the actor/tenancy
  one-liner, and "config = two env vars."
- Codex/Claude: add the skill → `lore capture/recall` immediately.

## 3. Slim MCP (revise L-MCP-05) + derived gating (L-MCP-06)

- Default `tools/list` returns the **core 6** (capture, recall, ack, search,
  read_page, upsert_page). Everything else (consolidation internals, patch plans,
  distillation, procedures, policies, traces, lint, heartbeat, graph analytics) is
  **either dropped from MCP (CLI/operator-only) or surfaced lazily** behind one
  `lore_advanced`/discovery tool so it costs no default context.
- Derive the read/write classification from each tool's `readOnlyHint`/
  `destructiveHint` (L-MCP-06) so the gate can't silently drift.
- `lore_overview` becomes the complete, runtime-derived index (was the original L-MCP-05).

## 4. SDK parity (L-SDK-02 → a/b) — same six verbs

- **L-SDK-02a (Hermes):** the plugin wraps the canonical client and exposes
  `lore_recall` + `lore_ack` (today it is capture-only over the legacy `/api/capture`).
- **L-SDK-02b (TypeScript):** add a `MemoryProvider` equivalent (capture/recall/ack)
  so TS integrators get the loop at all.

## 5. Capture-endpoint cleanup (L-API-02)

Pick the canonical capture endpoint the CLI/skill target (`/api/memory/capture`);
fold or clearly mark the legacy `/api/capture`; drop the server-overridden `actor`
field from `lore_capture`'s schema. Keeps the six-verb surface honest.

## Task graph (Flow)

```
L-IFACE-01 (epic: agent-first interface)
├─ L-CLI-01     agent memory CLI (6 verbs)         ← keystone
│    ↑ blocked_by L-RECALL-05 (recall must actually return results first)
├─ L-SKILL-01   rewrite skill around the CLI        ← blocked_by L-CLI-01
├─ L-MCP-05     slim MCP to core 6 + derived index  ← blocked_by L-CLI-01
│    └─ blocks L-MCP-06 (derive read/write gating)
├─ L-API-02     capture-endpoint cleanup
├─ L-SDK-02     align SDKs to the 6 verbs           ← blocked_by L-CLI-01
│    ├─ L-SDK-02a  Hermes full loop
│    └─ L-SDK-02b  TypeScript memory loop
└─ L-DOCS-02    docs: CLI-first + drift fixes        ← blocked_by L-CLI-01

standalone: L-OPS-02 (related to L-RECALL-05), L-READER-02 (human surface)
```

## Sequencing

1. **Now:** L-RECALL-05 (done, in review) — recall must return results.
2. **Next:** L-CLI-01 — the keystone; everything else aligns to its six-verb surface.
3. **Then in parallel:** L-SKILL-01, L-MCP-05/06, L-SDK-02a/b, L-API-02.
4. **Last:** L-DOCS-02 (document the surface once it's stable).
