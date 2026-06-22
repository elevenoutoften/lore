from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK_PATH = ROOT / "sdk" / "python"
if str(SDK_PATH) not in sys.path:
    sys.path.insert(0, str(SDK_PATH))

from lore_sdk.memory_provider import MemoryProvider  # noqa: E402


def test_memory_capture_accepts_structured_envelope_fields(client):
    response = client.post(
        "/api/memory/capture",
        json={
            "text": "Structured metadata envelope should be preserved.",
            "agent_name": "nyx",
            "task_id": "flow_000123",
            "decision_id": "decisions/memory-policy",
            "trace_id": "trace-abc",
            "tool_calls": [{"tool": "search", "query": "test"}],
            "constraints": ["do not change capture response schemas"],
            "policies_applied": ["L-MEM-02"],
            "metadata": {"confidence": "high"},
        },
    )

    assert response.status_code == 201, response.text
    page_id = response.json()["capture_id"]
    page = client.get(f"/api/pages/{page_id}").json()
    frontmatter = page["frontmatter"]

    assert frontmatter["source_task"] == "flow_000123"
    assert frontmatter["decision_id"] == "decisions/memory-policy"
    assert frontmatter["trace_id"] == "trace-abc"
    assert frontmatter["tool_calls"] == [{"tool": "search", "query": "test"}]
    assert frontmatter["constraints"] == ["do not change capture response schemas"]
    assert frontmatter["policies_applied"] == ["L-MEM-02"]
    assert frontmatter["confidence"] == "high"


def test_memory_capture_metadata_fallback_keeps_old_callers_working(client):
    response = client.post(
        "/api/memory/capture",
        json={
            "text": "Legacy metadata-only caller should still work.",
            "metadata": {
                "source_task": "flow_000456",
                "confidence": "high",
                "trace_id": "trace-legacy",
                "tool_calls": [{"tool": "read", "path": "README.md"}],
                "constraints": ["legacy constraint"],
                "policies_applied": ["legacy-policy"],
            },
        },
    )

    assert response.status_code == 201, response.text
    page_id = response.json()["capture_id"]
    page = client.get(f"/api/pages/{page_id}").json()
    frontmatter = page["frontmatter"]

    assert frontmatter["source_task"] == "flow_000456"
    assert frontmatter["confidence"] == "high"
    assert frontmatter["trace_id"] == "trace-legacy"
    assert frontmatter["tool_calls"] == [{"tool": "read", "path": "README.md"}]
    assert frontmatter["constraints"] == ["legacy constraint"]
    assert frontmatter["policies_applied"] == ["legacy-policy"]


def test_memory_capture_typed_fields_win_over_metadata(client):
    response = client.post(
        "/api/memory/capture",
        json={
            "text": "Typed structured envelope should override metadata values.",
            "task_id": "flow_typed",
            "trace_id": "trace-typed",
            "tool_calls": [{"tool": "typed"}],
            "constraints": ["typed constraint"],
            "policies_applied": ["typed-policy"],
            "metadata": {
                "source_task": "flow_metadata",
                "trace_id": "trace-metadata",
                "tool_calls": [{"tool": "metadata"}],
                "constraints": ["metadata constraint"],
                "policies_applied": ["metadata-policy"],
            },
        },
    )

    assert response.status_code == 201, response.text
    page_id = response.json()["capture_id"]
    page = client.get(f"/api/pages/{page_id}").json()
    frontmatter = page["frontmatter"]

    assert frontmatter["source_task"] == "flow_typed"
    assert frontmatter["trace_id"] == "trace-typed"
    assert frontmatter["tool_calls"] == [{"tool": "typed"}]
    assert frontmatter["constraints"] == ["typed constraint"]
    assert frontmatter["policies_applied"] == ["typed-policy"]


def test_memory_provider_capture_sends_structured_envelope(monkeypatch):
    provider = MemoryProvider(base_url="https://lore.example.test")
    captured_payload: dict = {}

    def fake_post(path, payload):
        captured_payload["path"] = path
        captured_payload["payload"] = payload
        return {"capture_id": "inbox/2026-05-22/sdk-test"}

    monkeypatch.setattr(provider, "_post_with_retry", fake_post)

    capture_id = provider.capture(
        "SDK capture with envelope fields.",
        agent_name="nyx",
        task_id="flow_000123",
        trace_id="trace-abc",
        tool_calls=[{"tool": "search", "query": "test"}],
        constraints=["stay compatible"],
        policies_applied=["L-MEM-02"],
        provenance={"source_paths": ["README.md"], "evidence": "SDK evidence"},
    )

    assert capture_id == "inbox/2026-05-22/sdk-test"
    assert captured_payload["path"] == "/api/memory/capture"
    assert captured_payload["payload"]["task_id"] == "flow_000123"
    assert captured_payload["payload"]["trace_id"] == "trace-abc"
    assert captured_payload["payload"]["tool_calls"] == [{"tool": "search", "query": "test"}]
    assert captured_payload["payload"]["constraints"] == ["stay compatible"]
    assert captured_payload["payload"]["policies_applied"] == ["L-MEM-02"]
    assert captured_payload["payload"]["provenance"] == {
        "source_paths": ["README.md"],
        "evidence": "SDK evidence",
    }


def test_memory_provider_default_base_url_is_8078(monkeypatch):
    """MemoryProvider with no base_url or env must default to :8078."""
    monkeypatch.delenv("LORE_BASE_URL", raising=False)
    provider = MemoryProvider()
    assert provider.base_url == "http://localhost:8078"


def test_memory_provider_page_promote_reads_top_level_id(monkeypatch):
    """page_promote must return the promote endpoint's top-level 'id', not 'None'."""
    provider = MemoryProvider(base_url="https://lore.example.test")

    def fake_post(path, payload):
        # The promote endpoint returns a flat PageDetail with top-level 'id',
        # not 'page_id' or nested 'page.id'.
        return {"id": "decisions/test-decision", "title": "Test Decision"}

    monkeypatch.setattr(provider, "_post_with_retry", fake_post)

    result_id = provider.page_promote("inbox/2026-06-18/test-capture")
    assert result_id == "decisions/test-decision"
    assert result_id != "None"
