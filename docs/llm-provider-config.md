# LLM Extraction Provider Configuration

## Default: extraction disabled (recommended: glm-5.1 on Ollama Cloud)

Extraction runs deterministic-only out of the box: the shipped default is
`LORE_LLM_PROVIDER=none`, so no LLM is called until a provider is configured
(via the env vars below or `PUT /api/settings/llm`). The recommended production
configuration is `glm-5.1` (or `minimax-m3`) on Ollama Cloud's OpenAI-compatible
API. The preferred path is the `/settings` web UI or one `PUT /api/settings/llm`
call (no env edits, hot reload); the env vars below remain a valid headless/Docker
bootstrap, and stored settings override env.

### Environment Variables

| Variable | Default | Recommended | Description |
|---|---|---|---|
| `LORE_LLM_PROVIDER` | `none` | `ollama` | LLM provider name; `none` disables LLM extraction |
| `LORE_LLM_MODEL` | *(empty)* | `glm-5.1` | Primary model; defaults to `glm-5.1` once a provider is set |
| `LORE_LLM_BASE_URL` | *(empty)* | `https://ollama.com/v1` | API base URL (Ollama Cloud OpenAI-compatible endpoint) |
| `LORE_LLM_EMBEDDING_MODEL` | *(empty)* | `embeddinggemma` | Optional OpenAI-compatible embedding model for dense retrieval |
| `LORE_LLM_API_KEY` | *(required to enable)* | — | API key (set via secret store, never committed) |
| `LORE_LLM_MAX_TOKENS` | `4096` | — | Max response tokens |
| `LORE_LLM_TEMPERATURE` | `0.3` | — | Sampling temperature |
| `LORE_LLM_ESCALATION_MODEL` | `minimax-m3` | — | Optional escalation model |
| `LORE_LLM_ESCALATION_API_KEY` | *(optional)* | — | Escalation API key (falls back to the primary key) |

### Escalation: MiniMax-M3

`minimax-m3` is configured only as an optional higher-capability escalation
provider for difficult/ambiguous batches. It is not the default extraction model.

### Optional dense retrieval

Set an embedding model in `/settings`, `PUT /api/settings/llm`, or
`LORE_LLM_EMBEDDING_MODEL` to enable semantic retrieval. Lore sends embedding
requests to the configured `base_url` with the primary API key, stores vectors
locally in sqlite-vec, and adds `semantic_similarity` to memory recall signals.
Changing the model rebuilds the dense index immediately. If either the embedding
model or API key is absent, Lore makes no embedding calls and retains its local
TF-IDF retrieval and existing recall ranking.

### No Anthropic

Lore extraction does **not** use Anthropic/Claude/Opus. No `ANTHROPIC_API_KEY` is required or configured.

### Provenance

When the LLM extraction path is active, extracted claims record:
- `model_version`: the actual model string (e.g., `glm-5.1`)
- `prompt_hash`: SHA-256 of the extraction prompt template (first 16 chars)
- `token_usage`: `{"prompt": N, "completion": M}` when the provider reports usage
- `observed_at`: set to the LLM call time (not capture time)
