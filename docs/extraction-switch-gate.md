# Extraction Model Switch Gate

## Current Production Default
The shipped `LoreConfig` default is extraction-disabled (`LORE_LLM_PROVIDER=none`).
The intended/recommended production configuration, set via env or `PUT /api/settings/llm`, is:
- **Primary**: qwen3.6-plus (OpenRouter)
- **Escalation**: GLM-5.1 (OpenRouter)

## Switching Criteria
A model can become the production default only when:
1. It passes the extraction eval suite (tests/test_eval_extraction.py) with all metrics green
2. The eval run is recorded with: `python tests/eval_extraction.py --live --provider <model> > eval_results_<model>_<date>.txt`
3. A Flow note records the run results and decision
4. The production default is rolled out by updating `LORE_LLM_MODEL` /
   `LORE_LLM_ESCALATION_MODEL` in the deploy config (the live `LoreConfig`
   default is env-driven in lore_app/config.py). The `DEFAULT_EXTRACTION_MODEL`
   / `DEFAULT_ESCALATION_MODEL` constants in lore_app/llm_provider.py are the
   no-config fallback only.

## Gemma 4 Restriction
Gemma 4 is available via a local endpoint but **must not** be promoted to production default until it passes the eval threshold with a recorded live run.

## Eval Metrics Thresholds
- JSON validity: 100%
- Schema validity: 100%
- Provenance completeness: >=50% per fixture
- Hallucination rate: 0 for must_not_contain_entities
