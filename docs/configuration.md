# Configuration

Lore reads configuration from environment variables through
`lore_app.config.LoreConfig`.

## Environment Variables

### Core storage and identity

| Variable | Default | Purpose |
| --- | --- | --- |
| `LORE_DATA_DIR` | `./data` | Base directory used to derive default database paths. |
| `LORE_APP_NAME` | `Lore` | FastAPI application title and UI name. |
| `LORE_APP_DESCRIPTION` | `Markdown-backed knowledge wiki for teams and agents.` | API and UI description. |
| `LORE_CONTENT_DIR` | `./data/pages` | Markdown page root. |
| `LORE_SEARCH_DB` | `./data/db/search.db` | SQLite search index path. |
| `LORE_VECTOR_DB` | `./data/db/vectors.db` | Vector/retrieval index path. |
| `LORE_LEDGER_DB` | `./data/db/ledger.db` | Durable claim-ledger database path. |
| `LORE_SETTINGS_DB` | `./data/db/settings.db` | Runtime settings database path. |
| `LORE_API_KEYS_DB` | `./data/db/api_keys.db` | Lore-owned API key registry path. |
| `LORE_WORKSPACES` | empty | JSON object defining mounted workspace storage overrides. |

### Network, auth, and branding

| Variable | Default | Purpose |
| --- | --- | --- |
| `LORE_HOST` | `127.0.0.1` | Host used by service launchers. Loopback by default so `auth_mode=none` starts safely; set `0.0.0.0` (with auth enabled) to expose the service. |
| `LORE_PORT` | `8000` | Port used by service launchers. |
| `LORE_AUTH_MODE` | `none` | Auth mode: `none`, `bearer`, `basic`, or `api_key`. |
| `LORE_AUTH_SECRET` | empty | Bearer token or `username:password` value for basic auth. |
| `LORE_METRICS_PUBLIC` | `false` | Expose `/metrics` without auth. By default, `/metrics` requires the configured auth mode and includes extraction-token gauges such as the total, last-batch, and recent-average rollups. |
| `LORE_SESSION_SECRET` | empty | Browser-session signing secret. When unset, Lore falls back to `LORE_AUTH_SECRET`, then a per-process random secret. |
| `LORE_ALLOW_INSECURE_BIND` | `false` | Acknowledge the risk of binding `auth_mode=none` to a non-loopback host. |
| `LORE_BRAND_TITLE` | `LORE` | Header brand label. |
| `LORE_BRAND_URL` | `/` | Header brand link. |
| `LORE_FAVICON_URL` | `/static/lore.css` | Favicon URL used by templates. |

### Capture, recall, consolidation, and retention

| Variable | Default | Purpose |
| --- | --- | --- |
| `LORE_WRITE_RATE_LIMIT` | `300` | Maximum write requests per rate-limit window. |
| `LORE_WRITE_RATE_WINDOW_SECONDS` | `60` | Write rate-limit window size in seconds. |
| `LORE_AUTO_CONSOLIDATE` | `true` | Run post-capture consolidation automatically in the background. |
| `LORE_CLAIM_FORGET_AFTER_FLOOR_DAYS` | `30` | Archive claims that remain at the `0.01` decay floor for this many days during consolidation; set `0` to disable automatic forgetting. |
| `LORE_VECTOR_RECONCILE_INTERVAL_SECONDS` | `300` | Dense-index reconciliation interval. |
| `LORE_AUDIT_RETENTION_DAYS` | `365` | Retention window for audit-log records. |

Operational, test, and evaluation captures are retained as audit inputs, not
durable knowledge. Heartbeat audit captures (`source_task:
heartbeat-self-audit`, page IDs containing `heartbeat-audit`) and disposable
test/smoke/probe captures must not remain as live extraction candidates. During
maintenance, Lore rejects candidates sourced only from disposable capture
provenance, archives already-active disposable candidates, and scrubs disposable
source IDs from mixed real/disposable provenance so curated backfill remains
recallable without re-polluting the graph.

### Maintenance, proxy trust, and browser policy

| Variable | Default | Purpose |
| --- | --- | --- |
| `LORE_MAINTENANCE_ENABLED` | `false` | Default enable state for the in-process maintenance scheduler (ledger decay + heartbeat captures + daily distillation). Overridable at runtime from the `/settings` page or `PUT /api/settings/maintenance` — no redeploy. |
| `LORE_MAINTENANCE_INTERVAL_SECONDS` | `86400` | Default maintenance scheduler interval in seconds. Also overridable at runtime via `/settings`. |
| `LORE_TRUSTED_HEADERS` | `false` | Trust reverse proxy headers for rate limiting and audit actor attribution. |
| `LORE_TRUSTED_PROXY_AUTH` | `false` | Allow trusted proxy identity headers to authenticate browser sessions. |
| `LORE_TRUSTED_PROXY_CIDRS` | empty | Space/comma-separated CIDR allowlist of proxy source IPs allowed to supply trusted identity headers. |
| `LORE_TRUSTED_PROXY_SECRET` | empty | Shared secret a trusted proxy may send as `X-Lore-Proxy-Secret`. |
| `LORE_CSP_POLICY` | empty | Optional CSP override string. |
| `LORE_EMBED_FRAME_ANCESTORS` | empty | Space/comma-separated `frame-ancestors` allowlist for embed surfaces. |

### Code ingest limits

| Variable | Default | Purpose |
| --- | --- | --- |
| `LORE_CODE_INGEST_ROOTS` | empty | Allowed root directories for `/api/code-ingest`. |
| `LORE_CODE_INGEST_MAX_FILES` | `500` | Maximum files scanned per ingest run. |
| `LORE_CODE_INGEST_MAX_DEPTH` | `10` | Maximum directory depth scanned per ingest run. |
| `LORE_CODE_INGEST_MAX_TOTAL_BYTES` | `52428800` | Maximum total bytes scanned per ingest run (50 MiB). |

### LLM and retrieval tuning

| Variable | Default | Purpose |
| --- | --- | --- |
| `LORE_LLM_PROVIDER` | `none` | Extraction/retrieval provider name; `none` disables LLM features. |
| `LORE_LLM_MODEL` | empty | Primary extraction model override. |
| `LORE_LLM_EMBEDDING_MODEL` | empty | Embedding model override for semantic retrieval. |
| `LORE_LLM_BASE_URL` | empty | Provider API base URL. |
| `LORE_LLM_API_KEY` | unset | Provider API key. |
| `LORE_LLM_MAX_TOKENS` | `4096` | Maximum response tokens for extraction calls. |
| `LORE_LLM_TEMPERATURE` | `0.3` | Extraction sampling temperature. |
| `LORE_LLM_TIMEOUT` | `60` | Extraction request timeout in seconds. |
| `LORE_LLM_MAX_RETRIES` | `3` | Maximum extraction retry attempts. |
| `LORE_LLM_ESCALATION_MODEL` | `minimax-m3` | Escalation/fallback model override. |
| `LORE_LLM_ESCALATION_API_KEY` | unset | Optional escalation-model API key. |

Inspect active configuration:

```bash
curl -sS -H "Authorization: Bearer $LORE_API_KEY" http://localhost:8078/api/config
lore-admin info
```

`GET /api/config` is admin-gated: it requires a Lore admin API key (or a trusted
admin session). When auth is disabled, the local operator is treated as admin.

## Auth Modes

No auth:

```bash
LORE_AUTH_MODE=none uvicorn lore_app.asgi:app
```

Bearer auth:

```bash
LORE_AUTH_MODE=bearer LORE_AUTH_SECRET="$LORE_TOKEN" uvicorn lore_app.asgi:app
curl -H "Authorization: Bearer $LORE_TOKEN" http://localhost:8078/api/pages
```

Basic auth uses `LORE_AUTH_SECRET` as the complete decoded credential string:

```bash
LORE_AUTH_MODE=basic LORE_AUTH_SECRET="admin:change-me" uvicorn lore_app.asgi:app
curl -u admin:change-me http://localhost:8078/api/pages
```

`/healthz`, `/healthz/config`, `/api/login`, `/api/logout`, and `/static`
remain public when auth middleware is enabled. `/metrics` requires auth unless
`LORE_METRICS_PUBLIC=true` is set.

Lore API key auth:

```bash
LORE_AUTH_MODE=api_key LORE_API_KEYS_DB=./data/api_keys.db uvicorn lore_app.asgi:app
curl -H "Authorization: Bearer $LORE_API_KEY" http://localhost:8078/api/pages
```

Create and rotate Lore keys through the `/api-keys` browser page or
`/api/api-keys` using a trusted admin session (`X-Axis-Admin: 1` from the
deployment auth gate, with `LORE_TRUSTED_PROXY_AUTH=true` enabled) or an
existing Lore admin key. Flow API keys are intentionally not accepted by Lore's
`api_key` mode.

### GPUBox/Caddy Deployment

A typical GPUBox/Caddy deployment uses `LORE_AUTH_MODE=api_key`,
`LORE_TRUSTED_HEADERS=true`, and `LORE_TRUSTED_PROXY_AUTH=true`.
API and MCP clients authenticate with `Authorization: Bearer` tokens backed by
Lore API keys. Browser users are authenticated by the GPUBox auth gate, then
Caddy forwards trusted identity headers to Lore for UI sessions.

## Multi-Workspace Setup

`LORE_WORKSPACES` mounts named workspaces as URL path prefixes. Workspace names
must be single path segments.

```bash
export LORE_WORKSPACES='{
  "team-a": {
    "content_dir": "/srv/lore/team-a/pages",
    "search_db": "/srv/lore/team-a/search.db",
    "vector_db": "/srv/lore/team-a/vectors.db",
    "ledger_db": "/srv/lore/team-a/ledger.db"
  },
  "team-b": {
    "content_dir": "/srv/lore/team-b/pages"
  }
}'
uvicorn lore_app.asgi:app --host 0.0.0.0 --port 8078
```

The default vault stays at `/`. Workspace `team-a` is available at `/team-a`.
Unset workspace fields inherit the base `LORE_CONTENT_DIR`, `LORE_SEARCH_DB`, or
`LORE_VECTOR_DB`, and `LORE_LEDGER_DB`.

Use `lore_app.asgi:app` as the uvicorn entry point. `lore_app.main` remains
import-safe and only exposes the application factory.

## Backup and Restore

Create a backup:

```bash
lore-admin backup --content-dir ./data/pages --output ./backups/lore-pages.tar.gz
```

Verify a backup:

```bash
lore-admin verify --input ./backups/lore-pages.tar.gz
```

Restore and rebuild the search index:

```bash
lore-admin restore \
  --input ./backups/lore-pages.tar.gz \
  --content-dir ./data/pages \
  --search-db ./data/db/search.db
```

Export or import JSON:

```bash
lore-admin export --content-dir ./data/pages --output pages.json
lore-admin import pages.json --content-dir ./data/pages
```
