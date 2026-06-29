"""Browser regression coverage for the lore2 SPA reader/index.

The reskin moved all index/reader rendering out of server-side templates and into
lore_app/static/lore2/app.js: the page list comes from /api/pages, the reader body
from /api/pages/{id}/rendered, the relationship panel from /api/pages/{id}/links,
and the provenance/sources panel from /api/pages/{id}. The rest of the suite
asserts those routes serve the SPA shell and that the JSON APIs return correct
data -- but only a real browser proves that app.js actually wires the shell + APIs
into the DOM. Without this, app.js could stop calling /rendered, mis-parse the
links payload, or drop a panel and the whole suite would still pass green.

This module closes that gap. It boots a real uvicorn server against the hermetic
conftest content, drives headless Chromium against `/` and a reader route, and
asserts the SPA renders the page list, the rendered markdown body, the
provenance/sources panel, and the relationship panel -- with no console errors and
a bounded (scale-guarded) context-graph fetch.

It is skipped automatically where Playwright (or its browser binary) is not
installed, so the default `.[test]` suite stays green; the CI `browser` job
installs Chromium and runs it for real (mirrors tests/test_graph_browser.py).
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing

import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _build_app(content_dir, tmp_path):
    from lore_app.config import LoreConfig
    from lore_app.main import create_app

    config = LoreConfig()
    config.content_dir = content_dir
    base = tmp_path / "reader-browser.db"
    config.search_db = base
    config.vector_db = base.with_name("vectors.db")
    config.ledger_db = base.with_name("ledger.db")
    config.api_keys_db = base.with_name("api_keys.db")
    config.settings_db = base.with_name("settings.db")
    config.trusted_headers = True
    config.auto_consolidate = False
    return create_app(config)


class _LiveServer:
    """Run the ASGI app on a real port in a background thread for the browser."""

    def __init__(self, app, port: int):
        self._config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.base_url = f"http://127.0.0.1:{port}"

    def __enter__(self) -> _LiveServer:
        self._thread.start()
        deadline = time.time() + 20
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("uvicorn server did not start in time")
        return self

    def __exit__(self, *_exc) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def test_spa_reader_renders_from_api(content_dir, tmp_path):
    """The lore2 SPA boots, lists pages, and deep-link-renders a reader from the APIs."""
    app = _build_app(content_dir, tmp_path)
    with _LiveServer(app, _free_port()) as server, sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except sync_api.Error as exc:  # browser binary not installed
            pytest.skip(f"Playwright Chromium not available: {exc}")
        try:
            graph_requests: list[str] = []
            console_errors: list[str] = []

            def _new_page():
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda exc: console_errors.append(str(exc)))
                page.on("request", lambda r: graph_requests.append(r.url) if "/api/context-graph" in r.url else None)
                return page

            # --- Index: app.js lists pages and builds the graph from the API ------
            home = _new_page()
            home.goto(f"{server.base_url}/", wait_until="domcontentloaded")
            home.wait_for_function(
                "() => { const r = document.getElementById('loreRoot');"
                " return r && /ExampleProject/.test(r.innerText)"
                " && window.__lore && window.__lore.s.gnodes.length > 0; }",
                timeout=15000,
            )
            home_text = home.inner_text("#loreRoot")
            assert "ExampleProject" in home_text
            assert "Workflow Engine" in home_text
            # The graph fetch must be bounded by the scale guard, not the raw endpoint.
            assert any("/api/context-graph?limit=" in u for u in graph_requests), graph_requests

            # --- Reader: deep-link opens the page and renders body + panels -------
            reader = _new_page()
            reader.goto(f"{server.base_url}/projects/example-project", wait_until="domcontentloaded")
            reader.wait_for_function(
                "() => { const r = document.getElementById('loreRoot');"
                " return r && r.innerText.includes('ExampleProject runs compute'); }",
                timeout=15000,
            )
            reader_text = reader.inner_text("#loreRoot")
            # rendered markdown body (from /api/pages/{id}/rendered)
            assert "ExampleProject runs compute" in reader_text
            # provenance/sources panel (from /api/pages/{id})
            assert "README.md" in reader_text
            # relationship panel (from /api/pages/{id}/links): the outgoing wiki-link target
            assert "Workflow Engine" in reader_text
            # The SPA opened the deep-linked page as a page reader, not a 404/shell.
            assert reader.evaluate("() => window.__lore.s.openId") == "projects/example-project"

            assert console_errors == [], f"console errors: {console_errors}"
        finally:
            browser.close()
