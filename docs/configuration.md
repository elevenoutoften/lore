# Configuration

Lore reads configuration from environment variables through
`lore_app.config.LoreConfig`.

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LORE_APP_NAME` | `Lore` | FastAPI application title and UI name. |
| `LORE_APP_DESCRIPTION` | `Markdown-backed knowledge wiki for teams and agents.` | API and UI description. |
| `LORE_CONTENT_DIR` | `./data/pages` | Markdown page root. |
| `LORE_SEARCH_DB` | `/data/db/search.db` | SQLite search index path. |
| `LORE_VECTOR_DB` | `/data/db/vectors.db` | Local vector/retrieval index path. |
| `LORE_API_KEYS_DB` | `/data/db/api_keys.db` | SQLite database for Lore-owned agent API keys. |
| `LORE_HOST` | `0.0.0.0` | Host used by service launchers. |
| `LORE_PORT` | `8000` | Port used by service launchers. |
| `LORE_AUTH_MODE` | `none` | Auth mode: `none`, `bearer`, `basic`, or `api_key`. |
| `LORE_AUTH_SECRET` | empty | Bearer token or `username:password` value for basic auth. |
| `LORE_BRAND_TITLE` | `LORE` | Header brand label. |
| `LORE_BRAND_URL` | `/` | Header brand link. |
| `LORE_FAVICON_URL` | `/static/lore.css` | Favicon URL used by templates. |
| `LORE_WORKSPACES` | empty | JSON object defining mounted workspace storage. |

Inspect active configuration:

```bash
curl -sS http://localhost:8078/api/config
lore info
```

## Auth Modes

No auth:

```bash
LORE_AUTH_MODE=none uvicorn lore_app.main:app
```

Bearer auth:

```bash
LORE_AUTH_MODE=bearer LORE_AUTH_SECRET="$LORE_TOKEN" uvicorn lore_app.main:app
curl -H "Authorization: Bearer $LORE_TOKEN" http://localhost:8078/api/pages
```

Basic auth uses `LORE_AUTH_SECRET` as the complete decoded credential string:

```bash
LORE_AUTH_MODE=basic LORE_AUTH_SECRET="admin:change-me" uvicorn lore_app.main:app
curl -u admin:change-me http://localhost:8078/api/pages
```

`/healthz` and `/static` remain public when auth middleware is enabled.

Lore API key auth:

```bash
LORE_AUTH_MODE=api_key LORE_API_KEYS_DB=./data/api_keys.db uvicorn lore_app.main:app
curl -H "Authorization: Bearer $LORE_API_KEY" http://localhost:8078/api/pages
```

Create and rotate Lore keys through the `/api-keys` browser page or
`/api/api-keys` using a trusted admin session (`X-Axis-Admin: 1` from the
deployment auth gate) or an existing Lore admin key. Flow API keys are
intentionally not accepted by Lore's `api_key` mode.

## Multi-Workspace Setup

`LORE_WORKSPACES` mounts named workspaces as URL path prefixes. Workspace names
must be single path segments.

```bash
export LORE_WORKSPACES='{
  "team-a": {
    "content_dir": "/srv/lore/team-a/pages",
    "search_db": "/srv/lore/team-a/search.db",
    "vector_db": "/srv/lore/team-a/vectors.db"
  },
  "team-b": {
    "content_dir": "/srv/lore/team-b/pages"
  }
}'
uvicorn lore_app.main:app --host 0.0.0.0 --port 8078
```

The default vault stays at `/`. Workspace `team-a` is available at `/team-a`.
Unset workspace fields inherit the base `LORE_CONTENT_DIR`, `LORE_SEARCH_DB`, or
`LORE_VECTOR_DB`.

## Backup and Restore

Create a backup:

```bash
lore backup --content-dir ./data/pages --output ./backups/lore-pages.tar.gz
```

Verify a backup:

```bash
lore verify --input ./backups/lore-pages.tar.gz
```

Restore and rebuild the search index:

```bash
lore restore \
  --input ./backups/lore-pages.tar.gz \
  --content-dir ./data/pages \
  --search-db ./data/search.db
```

Export or import JSON:

```bash
lore export --content-dir ./data/pages --output pages.json
lore import pages.json --content-dir ./data/pages
```
