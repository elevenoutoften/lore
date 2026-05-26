# LLM Extraction Provider Configuration

## Default: qwen3.6-plus (OpenRouter)

Lore uses `qwen3.6-plus` as its primary extraction model via OpenRouter's OpenAI-compatible API.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LORE_LLM_PROVIDER` | `openrouter` | LLM provider name |
| `LORE_LLM_MODEL` | `qwen3.6-plus` | Primary extraction model |
| `LORE_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | API base URL |
| `LORE_LLM_API_KEY` | *(required)* | API key (set via secret store, never committed) |
| `LORE_LLM_MAX_TOKENS` | `4096` | Max response tokens |
| `LORE_LLM_TEMPERATURE` | `0.3` | Sampling temperature |
| `LORE_LLM_ESCALATION_MODEL` | `glm-5.1` | Optional escalation model |
| `LORE_LLM_ESCALATION_API_KEY` | *(optional)* | Escalation API key |

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