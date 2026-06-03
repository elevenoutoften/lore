# Extraction Model Switch Gate

## Current Production Default
- **Primary**: qwen3.6-plus (OpenRouter)
- **Escalation**: GLM-5.1 (OpenRouter)

## Switching Criteria
A model can become the production default only when:
1. It passes the extraction eval suite (tests/test_eval_extraction.py) with all metrics green
2. The eval run is recorded with: `python tests/eval_extraction.py --live --provider <model> > eval_results_<model>_<date>.txt`
3. A Flow note records the run results and decision
4. The LoreConfig default is updated in lore_app/llm_provider.py

## Gemma 4 Restriction
Gemma 4 is available via a local endpoint but **must not** be promoted to production default until it passes the eval threshold with a recorded live run.

## Eval Metrics Thresholds
- JSON validity: 100%
- Schema validity: 100%
- Provenance completeness: >=50% per fixture
- Hallucination rate: 0 for must_not_contain_entities
