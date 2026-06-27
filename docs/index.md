# Lore Documentation

Lore is an agent memory backend: a fast, Markdown-backed memory store that
agents connect to with a token over HTTP and MCP to read, write, link, and
recall project knowledge. These docs cover the local service, REST API, SDKs,
deployment, and operating patterns. The browser UI is a minimal human surface
(readable wiki, graph, settings/keys); the API and MCP endpoint are the product.

## Start Here

- [Agent Memory Contract](agent-memory-contract.md): the canonical product
  surface — token-scoped connect, capture, and recency/salience-weighted recall
  over HTTP and MCP.
- [Quickstart](quickstart.md): install Lore, create a page, search, capture
  agent memory, and use the Python and TypeScript SDKs.
- [Configuration](configuration.md): environment variables, auth modes,
  workspaces, and backup/restore commands.
- [Concurrency Model](concurrency.md): SQLite/WAL writer behavior, benchmark
  harness, safe-writer ceiling, and Postgres/Kuzu upgrade triggers.
- [API Reference](api-reference.md): REST endpoints, schemas, errors, and rate
  limits.
- [MCP Examples](mcp-examples.md): JSON-RPC examples for MCP clients.
- [Security](security.md): auth, headers, validation, CSP, and rate limiting.
- [Deployment](deployment.md): Docker, systemd, reverse proxy, and environment
  setup.

## Guides

- [Agent Integration](agent-integration.md): REST, SDK, and MCP recipes for
  coding agents, CI bots, and custom tools.
- [API Examples](api-examples.md): expanded example payloads for common REST
  calls.
- [Capture Templates](capture-templates.md): capture patterns for project
  memory.
- [Governance](governance.md): policies, procedures, provenance, and reasoning
  traces.
- [Consolidation](consolidation.md): extraction, patch planning, and automated
  knowledge maintenance.
- [Distillation](distillation.md): session captures to daily notes, heartbeat
  self-audit.
- [Analytics](analytics-design.md): graph analytics, centrality, and community
  detection.
- [Policies](policies.md): policy engine, epistemic gates, and rule evaluation.
- [Design Department Brief](design-department-brief.md): concise product,
  feature, user-flow, and roadmap context for Web UI/UX design.

## SDKs

- [Python SDK](../sdk/python/README.md): install, connect, and use the Lore
  Python client.
- [TypeScript SDK](../sdk/typescript/README.md): npm package and TypeScript
  client. The browser [embed widget](../sdk/typescript/src/embed) ships
  alongside it.

## Release

- [Beta Release Checklist](beta-release-checklist.md): pre-release verification
  steps.

## Roadmap

The [Roadmap](roadmap.md) records the delivered memory-foundation and
security-hardening work and the remaining steps toward the beta release
milestone.

## Demo Data

The sample vault lives in [`../sample-vault`](../sample-vault). It demonstrates
valid frontmatter, wikilinks, service pages, architecture notes, guides, and
runbooks. Use `scripts/init-demo-vault.sh` to copy it into a Lore
content directory.
