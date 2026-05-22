# Changelog

All notable changes to Lore are documented here.

## [0.3.0-beta] - 2026-05-22

### Added
- Heartbeat review and self-audit capture flow with `GET /api/heartbeat`, `POST /api/heartbeat/captures`, and the `lore_heartbeat_audit` MCP tool
- Graph analytics over the context graph with `GET /api/graph/analytics` and the `lore_graph_analytics` MCP tool
- Expanded context graph APIs for enriched graphs, source edges, stats, neighbors, paths, and explanations
- Daily distillation and promotion endpoints plus MCP tools for daily capture review
- Consolidation worker endpoints and MCP tools for status, runs, rollback, patch planning, preview, apply, reject, and batch review
- Procedure candidate discovery, procedure creation/export/validation, and repeated-capture detection
- Trace, policy, provenance, decision, extraction, ledger, RAG, memory health, and API key management surfaces
- Epistemic status support for `hearsay`

### Changed
- RAG expansion now includes matched entities and richer graph context for retrieval explanations
- Context graph construction now includes pages, captures, candidates, plans, traces, actors, tasks, policies, tools, and sources with typed relationships
- Precedent search now honors `situation_type` filtering and returns consistent total counts
- Procedure candidate metadata is preserved when candidates are proposed from repeated captures
- Heartbeat audit captures create durable capture records for self-audit review

### Fixed
- Release-gate test expectations for epistemic statuses and MCP tool listing
- Trace list tests now assert against their own trace data instead of shared ledger totals
- Context graph tests now select candidate records from their own extraction batch

## [0.2.0] - 2026-05-01

### Added
- `GET /api/version` endpoint with service metadata
- Lint and capture intake endpoints and MCP tools
- Link graph and page links endpoints
- Browser reader with rendered Markdown, TOC, and internal link resolution
- MCP streamable-http transport with 9 tools and resource support
- CI workflow for automated test runs

## [0.1.0] - 2026-04-01

### Added
- Initial Lore service: Markdown-backed wiki with HTTP API
- Basic page CRUD, search, and catalog endpoints
- MCP initialize and tools/list support
