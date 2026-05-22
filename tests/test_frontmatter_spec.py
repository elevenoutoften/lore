from __future__ import annotations

from lore_app.frontmatter_spec import FRONTMATTER_SPEC, get_frontmatter_spec, validate_frontmatter


def test_frontmatter_spec_lists_expected_kinds():
    spec = get_frontmatter_spec()

    assert set(spec.specs) == {"project", "service", "decision", "runbook", "concept", "capture", "procedure", "page"}
    assert spec.specs["project"].required == ["title", "kind", "visibility", "summary", "owner"]
    assert spec.specs["procedure"].required == [
        "title",
        "kind",
        "visibility",
        "summary",
        "trigger",
        "steps",
        "schema_version",
        "validated",
        "validated_at",
        "author",
    ]
    assert "confidence" in spec.all_fields
    assert FRONTMATTER_SPEC["decision"].kind == "decision"


def test_frontmatter_validation_returns_missing_required_fields():
    missing = validate_frontmatter("decision", {"title": "Use Lore", "kind": "decision"})

    assert missing == ["visibility", "summary", "status", "decided_at", "deciders"]


def test_frontmatter_validation_falls_back_to_page_spec():
    missing = validate_frontmatter("unknown", {"title": "Loose Page"})

    assert missing == ["kind", "visibility"]
