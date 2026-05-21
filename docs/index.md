# Lore Documentation

Lore is a Markdown-backed knowledge wiki for humans, agents, and automation.
These docs cover the local service, REST API, SDKs, deployment, and operating
patterns.

## Start Here

- [Quickstart](quickstart.md): install Lore, create a page, search, capture
  agent memory, and use the Python and TypeScript SDKs.
- [Configuration](configuration.md): environment variables, auth modes,
  workspaces, and backup/restore commands.
- [API Reference](api-reference.md): REST endpoints, schemas, errors, and rate
  limits.
- [Security](security.md): auth, headers, validation, CSP, and rate limiting.
- [Deployment](deployment.md): Docker, systemd, reverse proxy, and environment
  setup.

## Existing Guides

- [Agent Integration](agent-integration.md): REST, SDK, and MCP recipes for
  coding agents, CI bots, and custom tools.
- [API Examples](api-examples.md): expanded example payloads for common REST
  calls.
- [Capture Templates](capture-templates.md): capture patterns for project
  memory.
- [MCP Examples](mcp-examples.md): JSON-RPC examples for MCP clients.

## Roadmap

The [Roadmap](roadmap.md) covers planned phases for memory foundation,
security hardening, and the beta release milestone.

## Demo Data

The sample vault lives in [`../sample-vault`](../sample-vault). It demonstrates
valid frontmatter, wikilinks, service pages, architecture notes, guides, and
runbooks. Use `scripts/init-demo-vault.sh` to copy it into a Lore
content directory.
