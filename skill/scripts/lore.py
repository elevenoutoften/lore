"""Run the repository's canonical Lore SDK CLI from the linked skill bundle."""

from __future__ import annotations

import sys
from pathlib import Path


def bundled_sdk_root(skill_script: str | Path | None = None) -> Path:
    """Resolve the sibling sdk/python checkout for install-via-link skills."""
    here = Path(skill_script) if skill_script is not None else Path(__file__)
    return here.resolve().parents[2] / "sdk" / "python"


def main(argv: list[str] | None = None) -> int:
    sdk_root = bundled_sdk_root()
    cli_path = sdk_root / "lore_sdk" / "cli.py"
    if not cli_path.is_file():
        print(
            f"lore skill wrapper could not find sdk/python at {sdk_root}",
            file=sys.stderr,
        )
        return 1

    sys.path.insert(0, str(sdk_root))
    from lore_sdk.cli import main as sdk_main

    return int(sdk_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
