from __future__ import annotations

from tests.eval_extraction_fixtures import EVAL_FIXTURES


def test_extraction_eval_ci_mode():
    """Run extraction eval suite in CI mode with mocked LLM responses.

    This is the CI gate for the extraction model switch gate.
    See docs/extraction-switch-gate.md for thresholds and switching criteria.
    """
    from tests.eval_extraction import run_eval

    results = run_eval(mode="ci")
    assert len(results) == len(EVAL_FIXTURES)
    for result in results:
        assert result["json_valid"], f"Fixture {result['name']}: JSON invalid"
        assert result["schema_valid"], f"Fixture {result['name']}: Schema invalid"
        assert result["provenance_completeness"] >= 0.5, f"Fixture {result['name']}: Provenance < 50%"

    # Eval gate: all fixtures must pass (switch gate threshold per docs/extraction-switch-gate.md)
    for result in results:
        assert result["hallucination_count"] == 0, f"Fixture {result['name']}: Hallucinated entities"
        assert result["missed_critical_claims"] == 0, f"Fixture {result['name']}: Missed critical claims"
