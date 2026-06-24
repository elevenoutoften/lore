## Epistemic Status Labels

Lore distinguishes **how knowledge was obtained** from **how certain it is**.

| Epistemic Status | Meaning | Example |
|---|---|---|
| `operator_declared` | Human operator stated this directly | "We use PostgreSQL for all services" |
| `retrieved` | Fetched from an external source or document | API docs, runbook, RFC |
| `inferred` | Deduced from other known facts | "Service X depends on Y" from dependency graph |
| `assumption` | LLM assumption without direct evidence | "This config is probably default" |
| `hearsay` | System-emitted (e.g. heartbeat) observation, not agent-chosen | Heartbeat capture summarizing recent activity |

### When agents choose labels

- **operator_declared**: Use when a human told you this in conversation
- **retrieved**: Use when you read it from a document, page, or API
- **inferred**: Use when you deduced it from multiple facts or graph traversal
- **assumption**: Use when no evidence supports the claim — flag for review

### When labels require review

- `assumption` labels should be reviewed before promotion to durable pages
- `inferred` labels on high-impact decisions should have supporting trace references
