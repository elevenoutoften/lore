"""Enable ``python -m lore_sdk`` as an alias for the ``lore`` CLI."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
