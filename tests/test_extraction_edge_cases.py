from __future__ import annotations

from lore_app.capture import CAPTURE_INTAKE_SUMMARY
from lore_app.extraction import _extract_capture, _first_meaningful_line, _wikilink_entities
from lore_app.ledger import LedgerDB
from lore_app.patch_planner import PatchPlanner, _CandidateBundle
from lore_app.repository import LoreRepository


def make_planner(tmp_path) -> PatchPlanner:
    repo = LoreRepository(tmp_path / "pages")
    repo.ensure_root()
    ledger = LedgerDB(tmp_path / "ledger.db")
    ledger.initialize()
    return PatchPlanner(repo, ledger)


def candidate_with_subject(subject: str) -> _CandidateBundle:
    return _CandidateBundle(
        row={
            "candidate_id": "candidate-1",
            "batch_id": "batch-1",
            "source_page_ids": [],
            "source_capture_ids": [],
        },
        claim={"subject": subject},
    )


def test_target_page_id_skips_invalid_capture_title_subject(tmp_path):
    planner = make_planner(tmp_path)

    assert planner._target_page_id(candidate_with_subject("Memory: memory/replace")) is None


def test_target_page_id_uses_valid_path_subject(tmp_path):
    planner = make_planner(tmp_path)

    assert planner._target_page_id(candidate_with_subject("services/lore")) == "services/lore"


def test_target_page_id_skips_capture_page_subject(tmp_path):
    planner = make_planner(tmp_path)

    assert planner._target_page_id(candidate_with_subject("inbox/2026-05-10/memory")) is None


def test_wikilink_entities_skip_label_first_documentation_placeholder():
    assert _wikilink_entities("See [[Label|page-id]]") == []


def test_wikilink_entities_extract_label_first_page_id():
    [entity] = _wikilink_entities("See [[Workflow Engine|services/workflow-engine]]")

    assert entity["page_id"] == "services/workflow-engine"
    assert entity["label"] == "Workflow Engine"


def test_wikilink_entities_extract_plain_page_id():
    [entity] = _wikilink_entities("See [[services/workflow-engine]]")

    assert entity["page_id"] == "services/workflow-engine"
    assert entity["label"] == "Workflow Engine"


def test_wikilink_entities_prefers_right_side_when_both_sides_are_plausible():
    [entity] = _wikilink_entities("See [[Alpha|beta]]")

    assert entity["page_id"] == "beta"
    assert entity["label"] == "Alpha"


def test_extract_capture_ignores_boilerplate_summary_for_claim_object(tmp_path):
    repo = LoreRepository(tmp_path / "pages")
    capture = repo.upsert_page(
        "inbox/2026-05-10/real-observation",
        f"""---
title: Memory: memory/replace
kind: capture
visibility: internal
status: draft
summary: {CAPTURE_INTAKE_SUMMARY}
confidence: high
---

# Memory: memory/replace

> Captured memory is rough intake for agent consolidation, not final Lore truth.

## Observation

Lore extraction should use this real observation.
""",
    )

    _, claims, _, _ = _extract_capture(repo, capture)

    assert claims[0].object == "Lore extraction should use this real observation."
    assert claims[0].object != CAPTURE_INTAKE_SUMMARY


def test_first_meaningful_line_skips_intake_warning():
    body = """> Captured memory is rough intake for agent consolidation, not final Lore truth.

Actual content.
"""

    assert _first_meaningful_line(body) == "Actual content."


def test_first_meaningful_line_returns_content_after_headings_and_intake_warning():
    body = """# Capture title

## Observation

> Captured memory is rough intake for agent consolidation, not final Lore truth.

Actual content after warning.
"""

    assert _first_meaningful_line(body) == "Actual content after warning."
