"""Deterministic preview server for the Claude Preview MCP.

Reuses the Start-Lore-Preview bootstrap helpers but runs on a fixed port with no
browser auto-open, so the preview tooling can attach to a known URL. Dev-only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "testing"))
sys.path.insert(0, str(REPO))

import lore2_preview as lp  # noqa: E402


def main() -> None:
    port = int(os.environ.get("LORE_PREVIEW_PORT", "8765"))
    lp._prepare_workspace()
    lp._configure_env(port)
    import uvicorn  # noqa: PLC0415

    uvicorn.run("lore_app.asgi:app", host=lp.HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
