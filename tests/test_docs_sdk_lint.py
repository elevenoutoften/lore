from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK_PATH = ROOT / "sdk" / "python"
sys.path.insert(0, str(SDK_PATH))

from lore_sdk import LoreClient, MemoryProvider  # noqa: E402
from lore_app.mcp.tools import TOOLS  # noqa: E402

DOCS = ROOT / "docs"
README = ROOT / "README.md"
CONFIG_PY = ROOT / "lore_app" / "config.py"

# Matches `client.<name>(` / `provider.<name>(` Python SDK call references in docs.
_SDK_CALL = re.compile(r"\b(?:client|provider)\.([a-z_][a-z0-9_]*)\s*\(")


def test_docs_reference_only_real_sdk_methods():
    """Every SDK method named in docs must exist on LoreClient or MemoryProvider.

    Guards against drift like the documented (non-existent) ``client.capture()`` —
    the real methods are ``LoreClient.create_capture`` and ``MemoryProvider.capture``.
    """
    missing: list[str] = []
    for md in sorted(DOCS.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        for name in sorted(set(_SDK_CALL.findall(text))):
            if getattr(LoreClient, name, None) is None and getattr(MemoryProvider, name, None) is None:
                missing.append(f"{md.name}: client/provider.{name}()")
    assert not missing, f"docs reference SDK methods that do not exist: {missing}"


def test_agent_memory_contract_uses_writer_not_editor_role():
    contract = (DOCS / "agent-memory-contract.md").read_text(encoding="utf-8")
    # The code enforces the 'writer' role; 'editor' is not a real role.
    assert "editor" not in contract.lower()


def test_configuration_doc_covers_all_config_env_vars():
    config_source = CONFIG_PY.read_text(encoding="utf-8")
    config_doc = (DOCS / "configuration.md").read_text(encoding="utf-8")

    env_vars = set(re.findall(r'os\.environ\.get\("((?:LORE_[A-Z0-9_]+))"', config_source))
    documented = set(re.findall(r"`(LORE_[A-Z0-9_]+)`", config_doc))

    missing = sorted(env_vars - documented)
    assert not missing, f"configuration.md is missing config env vars: {missing}"


def test_readme_mcp_tool_list_matches_registry():
    readme = README.read_text(encoding="utf-8")
    mcp_section = readme.split("## MCP", 1)[1].split("## ", 1)[0]
    documented = re.findall(r"^- `(lore_[^`]+)`$", mcp_section, flags=re.MULTILINE)
    expected = [tool["name"] for tool in TOOLS]

    assert documented == expected, "README MCP tool list drifted from lore_app.mcp.tools.TOOLS"
