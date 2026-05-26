# Security

Lore is designed to sit behind a trusted deployment boundary such as Caddy,
Tailscale, or an auth gate. The application still provides auth middleware,
security headers, validation, and write rate limits.

## Auth Modes

`LORE_AUTH_MODE=none` disables application auth. Use this only on trusted local
networks or behind an external gateway.

`LORE_AUTH_MODE=bearer` requires one static shared secret:

```http
Authorization: Bearer <LORE_AUTH_SECRET>
```

`LORE_AUTH_MODE=basic` requires HTTP basic credentials. The decoded
`username:password` string must exactly match `LORE_AUTH_SECRET`.

For `LORE_AUTH_MODE=bearer` and `LORE_AUTH_MODE=basic`, `LORE_AUTH_SECRET`
must be a non-empty string. If it is unset, empty, or whitespace-only, Lore
fails to start with a clear configuration error.

`LORE_AUTH_MODE=api_key` validates bearer tokens against Lore's own SQLite API
key registry at `LORE_API_KEYS_DB`. All requests must present a valid API key.
Trusted proxy browser sessions require explicit configuration.

API key management endpoints:

- `GET /api-keys`
- `GET /api/api-keys`
- `POST /api/api-keys`
- `POST /api/api-keys/{api_key_id}/revoke`

Those endpoints require an admin API key with role `admin`. Newly generated raw keys are shown once; only their hash and prefix are stored.

The auth middleware allows `/healthz` and `/static` without credentials so
health checks and static assets keep working.

## Trusted Headers

When Lore runs behind a reverse proxy (Caddy, Nginx, Tailscale), set
`LORE_TRUSTED_HEADERS=true` to trust the `X-Forwarded-For` and
`X-Lore-Actor` headers. This changes two behaviors:

- `X-Forwarded-For` is used as the rate-limit key. Without it, rate limiting
  keys on the direct client IP.
- `X-Lore-Actor` sets the actor name in audit logs for unauthenticated
  (none) mode requests coming through a trusted proxy. For authenticated
  requests, the auth middleware actor always takes precedence -- the header
  is ignored.

Without `LORE_TRUSTED_HEADERS=true`, these headers are ignored. This is the
safe default for direct exposure or local development.

## Trusted Proxy Auth

When Lore runs behind GPUBox/Caddy with `forward_auth`, set
`LORE_TRUSTED_PROXY_AUTH=true` to allow GPUBox-injected identity headers to
bypass auth middleware for browser/UI sessions.

This is separate from `LORE_TRUSTED_HEADERS`, which only affects rate limiting
and audit trails.

With `LORE_TRUSTED_PROXY_AUTH=true`, if a request arrives without a valid
`Authorization: Bearer` token, or after token auth fails, the middleware checks
for trusted proxy identity headers:

- `X-Axis-Admin: 1` grants the `admin` role and can manage API keys.
- `X-Lore-Agent` identifies an agent actor.
- `X-Axis-User` identifies a human user actor.
- `X-Lore-Actor` is a generic actor identifier with the lowest priority.

Actor priority is `X-Lore-Agent` > `X-Axis-User` > `X-Lore-Actor`.

Non-admin proxy sessions receive the `reader` role with the same write
restrictions as reader API keys.

Only enable `LORE_TRUSTED_PROXY_AUTH=true` when Lore runs behind a reverse proxy
that authenticates users and strips or replaces these headers before forwarding
requests.

### Combining LORE_TRUSTED_HEADERS and LORE_TRUSTED_PROXY_AUTH

A typical GPUBox deployment uses `LORE_AUTH_MODE=api_key`,
`LORE_TRUSTED_HEADERS=true`, and `LORE_TRUSTED_PROXY_AUTH=true`.
API and MCP clients continue to authenticate with `Authorization: Bearer`
tokens, while browser users are authenticated by the GPUBox/Caddy auth gate and
arrive at Lore with trusted proxy identity headers.

## Security Headers

Every response includes:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'`

The CSP allows same-origin scripts and styles plus inline styles/scripts used by
the current UI templates.

## Rate Limiting

Lore rate-limits write operations in memory at 30 requests per 60 seconds per
client key. The client key is the first `X-Forwarded-For` address when trusted
headers are enabled, or the direct client host.

Rate-limited operations include:

- `PUT`, `PATCH`, and `DELETE` under `/api/pages/`
- `POST /api/capture`
- `POST /api/captures/{page_id}/status`
- `POST /api/captures/{page_id}/promote`
- `POST /api/pages/{page_id}/stub`
- `POST /api/code-ingest/{service_id}`
- `POST /api/search/reindex`

Exceeded limits return HTTP `429` with `{"detail":"Rate limit exceeded."}`.

## Input Validation

Page IDs cannot be empty, absolute, contain null bytes, contain `..` segments,
or use segments that start with `.`. Repository normalization also requires each
path segment to contain letters, numbers, `.`, `_`, or `-`.

Page content is limited to 10,000,000 characters. Markdown rendering is
sanitized before browser delivery.

## Insecure Bind Guard

When `LORE_AUTH_MODE=none`, Lore refuses to bind to a non-loopback address
(everything except `127.0.0.1`, `localhost`, and `::1`) unless the operator
explicitly acknowledges the risk by setting `LORE_ALLOW_INSECURE_BIND=true`.

This prevents accidentally deploying an unauthenticated API on a public interface.
If you run Lore behind a trusted reverse proxy or an auth gate, set
`LORE_ALLOW_INSECURE_BIND=true` and ensure your deployment boundary enforces
authentication.

## HTML Sanitization

User-supplied markdown is rendered to HTML and sanitized using [nh3](https://pypi.org/project/nh3/),
a Python binding to Mozilla's ammonia sanitizer. The allowlist policy permits
safe formatting tags, table elements, and restricted link/image attributes.
JavaScript URLs, event handlers, and disallowed tags/attributes are stripped.

## CSP and Rendering

Lore stores Markdown as the source of truth and renders browser HTML through the
Markdown renderer and sanitizer. Internal Markdown links and wikilinks are
resolved to Lore routes; missing internal pages are marked so maintainers can
repair them.

## Code Ingest

The `/api/code-ingest` REST endpoints require an admin API key. The MCP
`lore_ingest_service` tool likewise requires the authenticated MCP request to
have the `admin` role, except when Lore is running with application auth
disabled.

Code ingest is disabled by default. To enable it, set `LORE_CODE_INGEST_ROOTS`
to a colon-separated list of directory roots that the ingester is allowed to
walk:

    LORE_CODE_INGEST_ROOTS=/data/pages:/opt/services

Any `source_dir` that is not a subdirectory of a configured root (after
resolving symlinks) is rejected with a 400 error. File count, depth, and
total size limits prevent resource exhaustion.
