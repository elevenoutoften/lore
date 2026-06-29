from __future__ import annotations

import json
import sys
import types
import urllib.parse
from unittest.mock import Mock


def import_hermes_sdk():
    agent_module = types.ModuleType("agent")
    memory_provider_module = types.ModuleType("agent.memory_provider")

    class MemoryProvider:
        pass

    memory_provider_module.MemoryProvider = MemoryProvider
    agent_module.memory_provider = memory_provider_module
    sys.modules.setdefault("agent", agent_module)
    sys.modules.setdefault("agent.memory_provider", memory_provider_module)

    from sdk.hermes import LoreMemoryProvider

    return LoreMemoryProvider


def make_provider():
    provider = import_hermes_sdk()()
    provider._client = Mock()
    provider._memory_client = Mock()
    provider._connected = True
    return provider


def capture_kwargs_for(
    target: str,
    action: str = "replace",
    metadata: dict | None = None,
) -> dict:
    provider = make_provider()
    provider.on_memory_write(
        action=action,
        target=target,
        content="some content",
        metadata=metadata,
    )
    return provider._memory_client.capture.call_args.kwargs


def test_tool_surface_includes_complete_memory_loop_and_page_tools():
    provider = make_provider()

    assert [schema["name"] for schema in provider.get_tool_schemas()] == [
        "lore_search",
        "lore_read",
        "lore_capture",
        "lore_recall",
        "lore_ack",
    ]


def test_capture_uses_canonical_memory_provider():
    provider = make_provider()
    provider._agent_name = "hermes"
    provider._memory_client.capture.return_value = "notes/hermes/2026-06-22/deployment-note"

    result = json.loads(
        provider.handle_tool_call(
            "lore_capture",
            {
                "observation": "Deployment uses the blue lane.",
                "title": "Deployment note",
                "namespace": "notes",
                "lane": "ops",
                "confidence": "high",
                "source_task": "flow_000885",
                "related_pages": ["services/lore"],
            },
        )
    )

    assert result == {"capture_id": "notes/hermes/2026-06-22/deployment-note"}
    provider._client.create_capture.assert_not_called()
    provider._memory_client.capture.assert_called_once_with(
        memory_text="Deployment uses the blue lane.",
        agent_name="hermes",
        namespace="notes",
        metadata={
            "tags": ["agent-memory"],
            "title": "Deployment note",
            "confidence": "high",
            "related_pages": ["services/lore"],
        },
        lane="ops",
        task_id="flow_000885",
    )


def test_recall_returns_full_diagnostic_envelope():
    provider = make_provider()
    provider._memory_client.recall_response.return_value = {
        "count": 0,
        "claims": [],
        "pending_captures": 1,
        "hint": "Capture is awaiting consolidation.",
    }

    result = json.loads(
        provider.handle_tool_call(
            "lore_recall",
            {"query": "deployment", "lane": "ops", "min_strength": 0.2, "limit": 5},
        )
    )

    assert result["pending_captures"] == 1
    assert "consolidation" in result["hint"].lower()
    provider._memory_client.recall_response.assert_called_once_with(
        "deployment",
        subject=None,
        lane="ops",
        min_strength=0.2,
        limit=5,
    )


def test_acknowledge_recall_uses_candidate_ids():
    provider = make_provider()
    provider._memory_client.acknowledge_recall.return_value = {
        "acknowledged_count": 2,
        "timestamp": "2026-06-22T00:00:00+00:00",
    }

    result = json.loads(provider.handle_tool_call("lore_ack", {"candidate_ids": ["c1", "c2"]}))

    assert result["acknowledged_count"] == 2
    provider._memory_client.acknowledge_recall.assert_called_once_with(["c1", "c2"])


def test_on_memory_write_suggests_lore_page_target():
    kwargs = capture_kwargs_for("projects/slapps")

    assert kwargs["metadata"]["suggested_target_page"] == "projects/slapps"
    assert kwargs["metadata"]["related_pages"] == ["projects/slapps"]


def test_on_memory_write_does_not_suggest_builtin_targets():
    provider = make_provider()

    provider.on_memory_write(action="add", target="memory", content="some content")
    provider._memory_client.capture.assert_not_called()

    provider._memory_client.capture.reset_mock()
    provider.on_memory_write(action="add", target="user", content="some content")
    provider._memory_client.capture.assert_not_called()


def test_on_memory_write_skips_builtin_targets():
    provider = make_provider()

    provider.on_memory_write(action="add", target="memory", content="some content")
    provider.on_memory_write(action="add", target="user", content="some content")

    provider._memory_client.capture.assert_not_called()


def test_on_memory_write_builtin_with_lore_worthy_creates_capture():
    kwargs = capture_kwargs_for("memory", action="add", metadata={"lore_worthy": True})

    assert kwargs["metadata"]["title"] == "Memory: memory/add"
    assert "suggested_target_page" not in kwargs["metadata"]
    assert "related_pages" not in kwargs["metadata"]


def test_on_memory_write_suggests_service_page_target():
    kwargs = capture_kwargs_for("services/lore")

    assert kwargs["metadata"]["suggested_target_page"] == "services/lore"
    assert kwargs["metadata"]["related_pages"] == ["services/lore"]


def test_authenticated_hermes_capture_recall_ack_loop(tmp_path):
    from fastapi.testclient import TestClient

    from lore_app.config import LoreConfig
    from lore_app.main import create_app

    provider = make_provider()
    from lore_sdk import MemoryProvider as LoreSdkMemoryProvider

    content_dir = tmp_path / "pages"
    content_dir.mkdir()
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = tmp_path / "search.db"
    config.vector_db = tmp_path / "vectors.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.settings_db = tmp_path / "settings.db"
    config.auth_mode = "api_key"
    config.auto_consolidate = True
    app = create_app(config)
    _, api_key = app.state.api_key_store.create_key(name="nyx", role="writer")

    with TestClient(app) as client:
        memory_client = LoreSdkMemoryProvider(base_url="http://lore.test", api_key=api_key)

        def route_request(method, path, data=None):
            parsed = urllib.parse.urlsplit(path)
            response = client.request(
                method,
                parsed.path,
                params=dict(urllib.parse.parse_qsl(parsed.query)),
                json=data,
                headers={"Authorization": f"Bearer {memory_client.api_key}"},
            )
            response.raise_for_status()
            return response.json() if response.content else {}

        memory_client._request = route_request
        provider._agent_name = "payload-agent"
        provider._memory_client = memory_client

        captured = json.loads(
            provider.handle_tool_call(
                "lore_capture",
                {
                    "observation": "HermesDurableLoopMarker uses the blue deployment lane.",
                    "title": "Hermes durable loop marker",
                    "namespace": "notes",
                    "lane": "ops",
                    "source_task": "flow_000885",
                },
            )
        )
        capture_page = app.state.repository.read_page(captured["capture_id"])
        assert capture_page is not None
        assert capture_page.id.startswith("notes/nyx/")
        assert capture_page.frontmatter["actor"] == "nyx"

        recalled = json.loads(
            provider.handle_tool_call("lore_recall", {"query": "HermesDurableLoopMarker", "limit": 5})
        )
        assert recalled["count"] >= 1
        claim = next(claim for claim in recalled["claims"] if claim["actor"] == "nyx")

        acknowledged = json.loads(provider.handle_tool_call("lore_ack", {"candidate_ids": [claim["candidate_id"]]}))
        assert acknowledged["acknowledged_count"] == 1

        after_ack = json.loads(
            provider.handle_tool_call("lore_recall", {"query": "HermesDurableLoopMarker", "limit": 5})
        )
        used_claim = next(item for item in after_ack["claims"] if item["candidate_id"] == claim["candidate_id"])
        assert used_claim["access_count"] == 1
