from __future__ import annotations

EVAL_FIXTURES = [
    {
        "name": "simple_capture",
        "capture": {
            "title": "Lore uses SQLite",
            "observation": "Lore stores all data in SQLite for durability and simplicity.",
            "actor": "nyx",
            "lane": "project",
            "observed_at": "2026-05-10T00:00:00+00:00",
            "suggested_target_page": "services/lore",
            "confidence": "high",
        },
        "expected": {
            "min_claims": 1,
            "min_entities": 1,
            "must_have_subject": "services/lore",
            "provenance_fields": ["actor", "lane", "observed_at"],
            "must_not_contain_entities": [],
        },
    },
    {
        "name": "multi_entity_capture",
        "capture": {
            "title": "Lore and Workflow integration",
            "observation": "Lore integrates with [[Workflow Engine|services/workflow-engine]] and [[ExampleProject|projects/example-project]] for task management.",
            "actor": "nyx",
            "lane": "project",
            "observed_at": "2026-05-10T00:00:00+00:00",
            "suggested_target_page": "services/lore",
            "confidence": "medium",
        },
        "expected": {
            "min_claims": 2,
            "min_entities": 2,
            "must_have_subject": "services/lore",
            "provenance_fields": ["actor", "lane"],
            "must_not_contain_entities": [],
        },
    },
    {
        "name": "contradiction_capture",
        "capture": {
            "title": "Lore storage correction",
            "observation": "Lore previously used PostgreSQL but now uses SQLite exclusively. The migration happened in 2025-Q4. This supersedes earlier documentation.",
            "actor": "nyx",
            "lane": "project",
            "observed_at": "2026-05-10T00:00:00+00:00",
            "suggested_target_page": "services/lore",
            "confidence": "high",
            "valid_from": "2025-10-01",
        },
        "expected": {
            "min_claims": 1,
            "min_entities": 1,
            "provenance_fields": ["actor", "lane", "observed_at", "valid_from"],
            "must_not_contain_entities": [],
        },
    },
    {
        "name": "policy_provenance_capture",
        "capture": {
            "title": "Auto-apply policy for Lore captures",
            "observation": "Policy auto-apply:v1 states that all lore captures with confidence=high and lane=project should be auto-accepted.",
            "actor": "nyx",
            "lane": "project",
            "observed_at": "2026-05-10T00:00:00+00:00",
            "suggested_target_page": "services/lore",
            "confidence": "high",
        },
        "expected": {
            "min_claims": 1,
            "min_entities": 1,
            "provenance_fields": ["actor", "lane"],
            "must_not_contain_entities": [],
        },
    },
    {
        "name": "ambiguous_noisy_capture",
        "capture": {
            "title": "Random notes",
            "observation": "I think maybe the thing does stuff sometimes? Not sure. Also testing.",
            "actor": "test",
            "lane": "draft",
            "confidence": "low",
            "observed_at": "2026-05-10T00:00:00+00:00",
            "suggested_target_page": "inbox/test",
        },
        "expected": {
            "min_claims": 0,
            "provenance_fields": ["actor", "lane"],
            "must_not_contain_entities": ["services/lore", "services/workflow-engine"],
        },
    },
]
