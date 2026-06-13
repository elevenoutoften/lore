# LLM Extraction Provider Configuration

## Default: extraction disabled (recommended: qwen3.6-plus via OpenRouter)

Extraction runs deterministic-only out of the box: the shipped default is
`LORE_LLM_PROVIDER=none`, so no LLM is called until a provider is configured
(via the env vars below or `PUT /api/settings/llm`). The recommended production
configuration is `qwen3.6-plus` through OpenRouter's OpenAI-compatible API.

### Environment Variables

| Variable | Default | Recommended | Description |
|---|---|---|---|
| `LORE_LLM_PROVIDER` | `none` | `openrouter` | LLM provider name; `none` disables LLM extraction |
| `LORE_LLM_MODEL` | *(empty)* | `qwen3.6-plus` | Primary model; defaults to `qwen3.6-plus` once a provider is set |
| `LORE_LLM_BASE_URL` | *(empty)* | `https://openrouter.ai/api/v1` | API base URL |
| `LORE_LLM_API_KEY` | *(required to enable)* | — | API key (set via secret store, never committed) |
| `LORE_LLM_MAX_TOKENS` | `4096` | — | Max response tokens |
| `LORE_LLM_TEMPERATURE` | `0.3` | — | Sampling temperature |
| `LORE_LLM_ESCALATION_MODEL` | `glm-5.1` | — | Optional escalation model |
| `LORE_LLM_ESCALATION_API_KEY` | *(optional)* | — | Escalation API key (falls back to the primary key) |

### Escalation: GLM-5.1

`glm-5.1` is configured only as an optional escalation provider for difficult/ambiguous batches. It is not the default extraction model.

### No Anthropic

Lore extraction does **not** use Anthropic/Claude/Opus. No `ANTHROPIC_API_KEY` is required or configured.

### Provenance

When the LLM extraction path is active, extracted claims record:
- `model_version`: the actual model string (e.g., `qwen3.6-plus`)
- `prompt_hash`: SHA-256 of the extraction prompt template (first 16 chars)
- `token_usage`: `{"prompt": N, "completion": M}` when the provider reports usage
- `observed_at`: set to the LLM call time (not capture time)