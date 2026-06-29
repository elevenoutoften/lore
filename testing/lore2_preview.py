"""Launch a local Lore test instance with the lore2 redesign UI + sample data.

Double-click `Start-Lore-Preview.bat` (repo root) to run this. It:
  1. Builds a throwaway content dir from testing/lore2-preview-vault (the 14 demo pages),
  2. Points Lore at it with auth disabled on loopback (LORE_AUTH_MODE=none, 127.0.0.1),
  3. Starts the server and opens your browser to the new UI.

Nothing here touches your real Lore data or the source vault — everything lives under
testing/.lore2-preview/ and is wiped + rebuilt on each launch. Stop with Ctrl+C.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VAULT = REPO / "testing" / "lore2-preview-vault"
RUN = REPO / "testing" / ".lore2-preview"
PAGES = RUN / "pages"
HOST = "127.0.0.1"
PORT_RANGE = range(8765, 8786)


def _free_port() -> int:
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}. Close other Lore instances.")


def _prepare_workspace() -> None:
    if not VAULT.is_dir():
        raise SystemExit(f"Sample vault not found: {VAULT}")
    # Fresh every launch so the search/link indexes rebuild from the current vault.
    shutil.rmtree(RUN, ignore_errors=True)
    try:
        shutil.copytree(VAULT, PAGES)
    except FileExistsError:
        # A locked leftover from a still-running instance; clear just the dbs.
        shutil.rmtree(RUN / "db", ignore_errors=True)
    (RUN / "db").mkdir(parents=True, exist_ok=True)


def _configure_env(port: int) -> None:
    os.environ.update(
        {
            "LORE_APP_NAME": "Lore",
            "LORE_DATA_DIR": str(RUN),
            "LORE_CONTENT_DIR": str(PAGES),
            "LORE_AUTH_MODE": "none",
            "LORE_HOST": HOST,
            "LORE_PORT": str(port),
            # No external LLM for a local preview: keeps it fully offline (FTS/link
            # indexes still build; dense embeddings + consolidation stay disabled).
            "LORE_LLM_PROVIDER": "none",
            "LORE_MAINTENANCE_ENABLED": "false",
            "LORE_AUTO_CONSOLIDATE": "false",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )


def _open_browser_when_ready(port: int) -> None:
    url = f"http://{HOST}:{port}/"

    def wait_then_open() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex((HOST, port)) == 0:
                    time.sleep(0.4)
                    print(f"\n  Lore preview is live at  {url}\n")
                    with contextlib.suppress(Exception):
                        webbrowser.open(url)
                    return
            time.sleep(0.3)
        print(f"\n  Server did not come up in time. Open {url} manually.\n")

    threading.Thread(target=wait_then_open, daemon=True).start()


def main() -> None:
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    port = _free_port()
    _prepare_workspace()
    _configure_env(port)

    try:
        import uvicorn
    except ModuleNotFoundError:
        raise SystemExit(
            "uvicorn is not installed for this Python. Activate the project venv "
            "(.venv) or run:  python -m pip install -e .[dev]"
        ) from None

    print("=" * 60)
    print("  Lore — lore2 redesign preview")
    print("=" * 60)
    print(f"  vault : {VAULT}")
    print(f"  data  : {RUN}  (throwaway, rebuilt each launch)")
    print(f"  url   : http://{HOST}:{port}/")
    print("  stop  : press Ctrl+C in this window")
    print("=" * 60)

    _open_browser_when_ready(port)
    # Pass the app as an import string so env vars (set above) are read when the
    # app is constructed inside uvicorn.
    uvicorn.run("lore_app.asgi:app", host=HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
