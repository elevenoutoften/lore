# Lore review — Flow tasks (filed)

The findings from the system review, filed as Flow tasks for project `lore`
(2026-06-22). Created via `tmp/file_flow_tasks.py`.

| ID | Key | Pri | Status | Title |
|---|---|---|---|---|
| [flow_000881](https://flow.axis.love/tasks/flow_000881) | **L-RECALL-05** | P0 | review (fixed) | Bind LLM-extractor claim actor to capture provenance + NULL-tolerant tenant recall |
| [flow_000882](https://flow.axis.love/tasks/flow_000882) | L-MCP-05 | P1 | backlog | Make `lore_overview` a complete, derived tool index (omits 19/59, incl. both RAG tools) |
| [flow_000883](https://flow.axis.love/tasks/flow_000883) | L-MCP-06 | P1 | backlog | Derive MCP read/write classification from tool annotations (replace hand-maintained `WRITE_TOOL_NAMES`) |
| [flow_000884](https://flow.axis.love/tasks/flow_000884) | L-API-02 | P1 | backlog | Disambiguate dual capture endpoints + stop advertising server-overridden `actor` on `lore_capture` |
| [flow_000885](https://flow.axis.love/tasks/flow_000885) | L-SDK-02 | P1 | backlog | Bring Hermes plugin + TypeScript SDK up to the full capture→recall→ack loop |
| [flow_000886](https://flow.axis.love/tasks/flow_000886) | L-OPS-02 | P1 | backlog | `consolidation/run` default is a silent no-op (`dry_run=true` + `max_auto_apply=0`) |
| [flow_000887](https://flow.axis.love/tasks/flow_000887) | L-READER-02 | P2 | backlog | Reader front door on `/`; split Read vs Operate nav; hide raw-JSON links |
| [flow_000888](https://flow.axis.love/tasks/flow_000888) | L-DOCS-02 | P2 | backlog | Fix doc/code drift (contract actor-scope warning, rate limit 30→300, quickstart capture shape, missing tools, env coverage) |

**L-RECALL-05 (flow_000881)** is already implemented this session on branch
`fix/llm-extractor-actor-recall` (commit `50ae06c`) and filed as `review`.

See [testing/system-review.md](system-review.md) §6 for the full rationale behind each.
