from __future__ import annotations

import sys
import types
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
    return provider._client.create_capture.call_args.kwargs


def test_on_memory_write_suggests_lore_page_target():
    kwargs = capture_kwargs_for("projects/slapps")

    assert kwargs["suggested_target_page"] == "projects/slapps"
    assert kwargs["related_pages"] == ["projects/slapps"]


def test_on_memory_write_does_not_suggest_builtin_targets():
    provider = make_provider()

    provider.on_memory_write(action="add", target="memory", content="some content")
    provider._client.create_capture.assert_not_called()

    provider._client.create_capture.reset_mock()
    provider.on_memory_write(action="add", target="user", content="some content")
    provider._client.create_capture.assert_not_called()


def test_on_memory_write_skips_builtin_targets():
    provider = make_provider()

    provider.on_memory_write(action="add", target="memory", content="some content")
    provider.on_memory_write(action="add", target="user", content="some content")

    provider._client.create_capture.assert_not_called()


def test_on_memory_write_builtin_with_lore_worthy_creates_capture():
    kwargs = capture_kwargs_for("memory", action="add", metadata={"lore_worthy": True})

    assert kwargs["title"] == "Memory: memory/add"
    assert "suggested_target_page" not in kwargs
    assert "related_pages" not in kwargs


def test_on_memory_write_suggests_service_page_target():
    kwargs = capture_kwargs_for("services/lore")

    assert kwargs["suggested_target_page"] == "services/lore"
    assert kwargs["related_pages"] == ["services/lore"]
