from __future__ import annotations

from tests.eval_extraction_fixtures import EVAL_FIXTURES


def test_extraction_eval_ci_mode():
    """Run extraction eval suite in CI mode with mocked LLM responses."""
    from tests.eval_extraction import run_eval

    results = run_eval(mode="ci")
    assert len(results) == len(EVAL_FIXTURES)
    for result in results:
        assert result["json_valid"], f"Fixture {result['name']}: JSON invalid"
        assert result["schema_valid"], f"Fixture {result['name']}: Schema invalid"
        assert result["provenance_completeness"] >= 0.5, f"Fixture {result['name']}: Provenance < 50%"
