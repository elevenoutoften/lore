from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK_PATH = ROOT / "sdk" / "python"
sys.path.insert(0, str(SDK_PATH))

from lore_sdk import LoreClient, MemoryProvider  # noqa: E402

DOCS = ROOT / "docs"

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
