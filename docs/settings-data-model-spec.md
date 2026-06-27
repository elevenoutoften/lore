# Lore Settings & Configuration Data-Model Specification

Companion to [ui-data-model-spec.md](ui-data-model-spec.md). That document covers the reader/search/graph surfaces; this one covers **settings & configuration** — what a human can configure, on which surface, through what interface. Same rules: field tables and "not in the UI" notes are **hard facts** from the code; anything labelled *option* or *precedent* is a menu for the design team, not a mandate. No visual design is prescribed.

---

## 1. The three configuration layers (and who owns each)

Lore configuration is **layered**, and only two of the three layers are human-facing in a browser:

| Layer | Where it lives | Who sets it | Surface | In a browser UI? |
|---|---|---|---|---|
| **1. Deploy/operator config** | `LORE_*` environment variables (`config.py`) | Operator at deploy time | env file / systemd / Docker `-e` | **No** — never in the UI |
| **2. Runtime model & maintenance settings** | `SettingsStore` SQLite (`runtime_settings` table) | Human (admin) at runtime | **`/settings` page** + `/api/settings/llm`, `/api/settings/maintenance` | **Yes** |
| **3. API keys** | API-keys SQLite DB | Human (admin) at runtime | **`/api-keys` page** + `/api/api-keys` | **Yes** |

Key relationship: **Layer 2 overrides Layer 1 for the LLM provider.** `merged_llm_config(config, settings_store)` merges the runtime `/settings` values *on top of* the `LORE_LLM_*` env defaults — so the UI is the live override, env is the fallback (`routes/settings.py:137-154`). The same pattern now governs the **background maintenance scheduler**: `merged_maintenance_config(config, settings_store)` overlays `/api/settings/maintenance` on the `LORE_MAINTENANCE_*` env defaults (L-CFG-02, see §2.6).

Agents do **not** set configuration. They may *read* the (masked) model settings with any valid key (`GET /api/settings/llm`), and they *use* API keys — but creating keys and changing the model are admin/human actions. So the human settings surface is exactly **two pages**.

---

## 2. Human surface A — Model settings (`/settings`)

The single page for choosing the LLM/embedding provider Lore uses for extraction, recall scoring, and (optionally) dense embeddings. Landed as L-CFG-01.

### 2.1 Endpoints & auth

| Method / path | Auth | Purpose |
|---|---|---|
| `GET /settings` | page (session or local operator) | Renders `settings.html` |
| `GET /api/settings/llm` | **any valid Lore key** (`require_lore_key`) | Read current settings (secrets masked) |
| `PUT /api/settings/llm` | **admin only** (`require_lore_key_admin`) | Partial update; hot-reloads the LLM client |
| `DELETE /api/settings/llm` | **admin only** | Clear all runtime LLM settings → revert to env/code defaults |

> Local-operator bypass: under `LORE_AUTH_MODE=none` **on a loopback request** (`none_mode_local_operator`), the operator is treated as admin so the page works out of the box. This bypass is gated to local requests — a non-loopback `none` bind does **not** expose the masked settings or the PUT to the network.

### 2.2 Read model — `LlmSettingsResponse` (GET/PUT return)

Secrets are **never returned** — only a `*_configured` boolean and a masked `*_hint`.

| Field | Type | Notes |
|---|---|---|
| `provider` | string | e.g. `openai`, `ollama`, `anthropic`, … (the merged effective provider) |
| `model` | string | Primary extraction/generation model |
| `embedding_model` | string | Embedding model (`""` if none) |
| `embeddings_enabled` | bool | True only when an embedding model **and** an api_key are set |
| `base_url` | string | Provider base URL (`""` if default) |
| `api_key_configured` | bool | Whether a primary key is set |
| `api_key_hint` | string | Masked: `"****"` (≤8 chars) or `"****"+last4` — never the secret |
| `escalation_model` | string | Optional stronger model for escalation |
| `escalation_api_key_configured` | bool | |
| `escalation_api_key_hint` | string | Masked, as above |
| `max_tokens` | int | |
| `temperature` | float | |
| `timeout_seconds` | float | |
| `max_retries` | int | |

### 2.3 Write model — `LlmSettingsUpdate` (PUT body)

**Partial / patch semantics** — send only the fields you want to change; every field is optional (`None` = leave unchanged).

| Field | Type | Secret? |
|---|---|---|
| `provider` | string | no |
| `model` | string | no |
| `embedding_model` | string | no |
| `base_url` | string | no |
| `api_key` | string | **yes** (stored masked, never echoed) |
| `escalation_model` | string | no |
| `escalation_api_key` | string | **yes** |
| `max_tokens` | int | no |
| `temperature` | float | no |
| `timeout_seconds` | float | no |
| `max_retries` | int | no |

### 2.4 Behavior the design team inherits

- **Hot reload, no restart.** A successful `PUT`/`DELETE` calls `rebuild_llm_client(app)` — the new provider is live on the next request (`routes/settings.py:99-101,116-118`).
- **Masking.** Secret fields render as `****last4` (or `****` for ≤8 chars) via `mask_secret`. The UI must treat the secret inputs as write-only: show the masked hint + `*_configured` state, accept a new value to replace, but never display the stored secret.
- **Reset to defaults** = `DELETE` (clears Layer 2 so Layer 1 env/code defaults take over).
- **Storage.** `SettingsStore` is SQLite (`runtime_settings`: `key`, `value`, `secret`, `updated_at`), WAL, file mode `0600`. Secret rows carry `secret=1` and are masked even in `get_all_masked()`.
- **A status indicator is supportable** from `provider` + `model` + `*_configured` + `embeddings_enabled` (e.g. "configured / using env default / no key").

### 2.5 The form, in data terms
A model-settings form maps 1:1 to `LlmSettingsUpdate` (provider, model, embedding model, base URL, primary key, escalation model, escalation key, max_tokens, temperature, timeout, max_retries), pre-filled from `LlmSettingsResponse` with the two key fields shown as masked write-only inputs. Save = `PUT`; Reset = `DELETE`.

### 2.6 Companion panel — Background maintenance (same `/settings` page)

The in-process maintenance scheduler (ledger decay + heartbeat self-audit captures + daily distillation) is **also a Layer-2 runtime setting**, rendered as a second panel on the same page. Landed as L-CFG-02. Before this it was env-only (`LORE_MAINTENANCE_ENABLED`), which forced an env edit + redeploy to turn on; now an admin toggles it live.

| Method / path | Auth | Purpose |
|---|---|---|
| `GET /api/settings/maintenance` | **any valid Lore key** (`require_lore_key`) | Read effective scheduler state |
| `PUT /api/settings/maintenance` | **admin only** (`require_lore_key_admin`) | Partial update; restarts the scheduler thread |
| `DELETE /api/settings/maintenance` | **admin only** | Clear overrides → revert to env defaults |
| `POST /api/settings/maintenance/run-now` | **admin only** | Dispatch one pass now (background); returns `202` |

**Read model — `MaintenanceSettingsResponse`:** `enabled` (bool, merged effective), `interval_seconds` (int, merged effective), `running` (bool — whether the scheduler thread is alive), `last_maintenance_at` (str|None — ISO timestamp of the last completed pass, also on `/api/memory/health`), `enabled_source` (`"settings"` if a stored override is in effect, else `"environment"` — lets the UI show whether Reset will change behavior).

**Write model — `MaintenanceSettingsUpdate`** (partial): `enabled` (bool|None), `interval_seconds` (int|None, **≥60** — the floor keeps `enabled` the real on/off switch; `0` would silently no-op the scheduler).

**Behavior the design team inherits:**
- **Hot reload, no restart.** `PUT`/`DELETE` call `restart_maintenance_scheduler(app)`, which swaps the background thread; its handle lives on `app.state` so the toggle takes effect without a process restart.
- **Run now is async.** It dispatches `run_maintenance_tick` on a one-shot background thread and returns `202` immediately (a first tick can back-distill many days and exceed an edge-proxy timeout if run inline). The UI polls `GET /api/settings/maintenance` until `last_maintenance_at` advances.
- **Reset to defaults** = `DELETE` (clears the two stored rows; env/code defaults take over), same idiom as model settings.
- A **status indicator** is supportable from `running` + `enabled` + `last_maintenance_at` (e.g. "running / enabled but idle / off").

---

## 3. Human surface B — API keys (`/api-keys`)

The page for minting and revoking the bearer keys agents use to talk to Lore.

### 3.1 Endpoints & auth (all admin-only)

| Method / path | Purpose |
|---|---|
| `GET /api-keys` | Renders `api_keys.html` |
| `GET /api/api-keys` | List keys (never includes the raw token) |
| `POST /api/api-keys` | Create a key → returns the raw token **once** |
| `POST /api/api-keys/{id}/revoke` | Revoke a key |
| `POST /api/login` | Exchange a `lore_` key for a read-only browser session cookie (public) |
| `POST /api/logout` | Clear the session cookie |

All key-management endpoints require **admin** (`require_lore_key_admin`); same local-operator-in-`none`-mode bypass as §2.1.

### 3.2 Roles — `LoreApiKeyRole`

| Role | Capability |
|---|---|
| `admin` | Full access incl. key management + model settings (PUT/DELETE) |
| `writer` | Read + write (captures, pages, recall, ack) |
| `reader` | Read-only (reader HTML + read MCP tools); **403 on any write tool** |

### 3.3 Create / list models

**`LoreApiKeyCreate`** (POST body): `name` (1–120, required), `description` (≤500, default `""`), `role` (enum, default `writer`).

**`LoreApiKeyResponse`** (list/revoke return): `id`, `name`, `description`, `role`, `key_prefix`, `created_at`, `revoked_at` (`null` until revoked). **No raw token.**

**`LoreApiKeyCreateResponse`** (create return): all of the above **plus** `api_key` — the full raw token, **shown exactly once**. The UI must surface a copy affordance and warn it won't be shown again; thereafter only `key_prefix` identifies the key.

### 3.4 Browser session (`/api/login`)
`LoginRequest` = `{ api_key: string (1–200) }`. On success the server sets a signed, **HttpOnly, SameSite=strict** cookie (`lore_session`, 24h TTL, `secure` on HTTPS) granting **read-only** browser access — writes stay token-only. This is how a human "logs in" by pasting a `lore_` key when not behind SSO. *(Owner UX note: pasting a token after SSO is a known rough edge — see the UAT script; on the live SSO deployment the proxy role may cover this.)*

---

## 4. Operator/env-only configuration (NOT in the UI)

Everything below is **Layer 1** — set via `LORE_*` environment variables at deploy time (`config.py`), never in a browser. Listed so the design team knows the boundary; the authoritative reference is [configuration.md](configuration.md).

| Category | Examples (`LORE_*`) |
|---|---|
| Storage / paths | `DATA_DIR`, `CONTENT_DIR`, `SEARCH_DB`, `VECTOR_DB`, `LEDGER_DB`, `API_KEYS_DB`, `SETTINGS_DB` |
| Network / bind | `HOST`, `PORT` |
| Auth & secrets | `AUTH_MODE`, `AUTH_SECRET`, `SESSION_SECRET`, `ALLOW_INSECURE_BIND`, `METRICS_PUBLIC` |
| Branding | `BRAND_TITLE`, `BRAND_URL`, `FAVICON_URL`, `APP_NAME` |
| Rate limiting | `WRITE_RATE_LIMIT`, `WRITE_RATE_WINDOW_SECONDS` |
| Memory lifecycle | `AUTO_CONSOLIDATE`, `CLAIM_FORGET_AFTER_FLOOR_DAYS`, `VECTOR_RECONCILE_INTERVAL_SECONDS`, `MAINTENANCE_ENABLED`†, `MAINTENANCE_INTERVAL_SECONDS`†, `AUDIT_RETENTION_DAYS` |
| Security / proxy | `TRUSTED_HEADERS`, `TRUSTED_PROXY_AUTH`, `TRUSTED_PROXY_CIDRS`, `TRUSTED_PROXY_SECRET`, `CSP_POLICY`, `EMBED_FRAME_ANCESTORS` |
| Code ingest | `CODE_INGEST_ROOTS`, `CODE_INGEST_MAX_FILES`, `CODE_INGEST_MAX_DEPTH`, `CODE_INGEST_MAX_TOTAL_BYTES` |
| LLM **defaults** (overridden by `/settings`) | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_EMBEDDING_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`, `LLM_TIMEOUT`, `LLM_MAX_RETRIES`, `LLM_ESCALATION_MODEL`, `LLM_ESCALATION_API_KEY` |
| Multi-workspace | `WORKSPACES` |

† `MAINTENANCE_ENABLED` and `MAINTENANCE_INTERVAL_SECONDS` are Layer-1 **defaults** that are now overridable at runtime via `/settings` (`/api/settings/maintenance`); see §2.6.

A small slice of operator setup has a **CLI**, not a UI: `lore-admin` (key bootstrap before a server is up, backup/restore, consolidate). That is intentionally out of the browser surface.

---

## 5. Access summary — who can see/do what

| Action | reader key | writer key | admin key | local operator (`none`+loopback) | session cookie |
|---|---|---|---|---|---|
| `GET /api/settings/llm` (masked) | ✅ | ✅ | ✅ | ✅ | ✅ (read) |
| `PUT`/`DELETE /api/settings/llm` | ❌ | ❌ | ✅ | ✅ | ❌ (read-only) |
| `GET /api/settings/maintenance` | ✅ | ✅ | ✅ | ✅ | ✅ (read) |
| `PUT`/`DELETE` + run-now `/api/settings/maintenance` | ❌ | ❌ | ✅ | ✅ | ❌ (read-only) |
| List / create / revoke API keys | ❌ | ❌ | ✅ | ✅ | ❌ |
| `POST /api/login` (paste key) | n/a (public) | — | — | — | — |

---

## 6. Sample payloads

**Model settings — `GET /api/settings/llm` → `LlmSettingsResponse`** (secrets masked):
```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "embedding_model": "text-embedding-3-small",
  "embeddings_enabled": true,
  "base_url": "",
  "api_key_configured": true,
  "api_key_hint": "****a1b2",
  "escalation_model": "gpt-4o",
  "escalation_api_key_configured": false,
  "escalation_api_key_hint": "",
  "max_tokens": 1024,
  "temperature": 0.2,
  "timeout_seconds": 30.0,
  "max_retries": 3
}
```

**Model settings — `PUT /api/settings/llm` body** (partial; only what changed):
```json
{ "model": "gpt-4o", "temperature": 0.1, "api_key": "sk-...new..." }
```

**Create key — `POST /api/api-keys` body**:
```json
{ "name": "nyx-agent", "description": "Hermes runtime key", "role": "writer" }
```

**Create key — response (`LoreApiKeyCreateResponse`, token shown once)**:
```json
{
  "id": "lore_key_0245201dfdcf",
  "name": "nyx-agent",
  "description": "Hermes runtime key",
  "role": "writer",
  "key_prefix": "lore_A1vG",
  "created_at": "2026-06-25T12:00:00Z",
  "revoked_at": null,
  "api_key": "lore_A1vGB5QEZEzQHtPCUvdxiwLFSZw6Tc6VfkogUg0iopE"
}
```

---

## 7. Design notes & cross-system precedents (references, not requirements)

- **mem0** — a management dashboard (memories + user/agent/run filtering); settings are mostly config/SDK, not a rich UI.
- **Honcho** — **no settings UI** at all (API/SDK only; the hosted dashboard is billing). Another data point that a settings UI can be very thin.
- **OpenPaw / KuzuMemory** — config via files/env, no settings UI.

Given the owner's minimal-UI goal, Lore's **two** settings pages are already more UI than most peers ship. Reasonable design stances the data supports:
- Keep both pages, redesigned to match the product's design system (model settings + keys are the only genuinely human config).
- Or fold them into one compact "Admin / Setup" surface, since both are admin-only and low-frequency.
- The secret-input pattern (masked hint + write-only replace + `*_configured` state) is the one interaction that must be preserved exactly, on whatever layout.

## Key constraints the design team must honor
- **Secrets are write-only** — the API returns only `*_configured` + a masked `*_hint`; never render a stored secret. New value replaces; empty = unchanged.
- **The created API token is shown exactly once** — design the copy-now affordance + "won't be shown again" warning; afterwards only `key_prefix` identifies a key.
- **Settings changes are admin-only and hot-reload** — no restart, no save-then-reboot affordance needed.
- **Model settings override env defaults**; "Reset to defaults" means `DELETE` (fall back to env), not blank.
- **Roles are `admin`/`writer`/`reader`** — the create form's role selector uses exactly these.
- **Most configuration is not here** — deploy/operator settings are env-only by design; don't design UI for them.
