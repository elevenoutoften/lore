"""Browser regression coverage for the L-GRAPH-01 knowledge-graph showpiece.

The previous L-GRAPH-01 review failed because the vis-network canvas rendered
blank in the first viewport: the shell used `min-height: 100vh` (an indefinite
height) so the grid 1fr track never resolved, `#graphNetwork { height: 100% }`
fell back to auto, and vis-network grew its canvas to the full page height
(~2522px) -- pushing every node and the status text below the fold. The whole
pytest suite still passed because nothing rendered the page in a real browser.

This module closes that gap. It boots a real uvicorn server against the
hermetic conftest content, drives a headless Chromium against `/graph`, and
asserts the things only a layout engine can tell us:

* the stage/canvas/body stay bounded to one viewport (no thousands-of-pixels
  growth) on both desktop and mobile,
* the graph actually rendered nodes and the status text is visible in the first
  viewport,
* there are no console/page errors,
* entering and leaving the ego/tree view still works.

It is skipped automatically where Playwright (or its browser binary) is not
installed, so the default `.[test]` suite stays green; the CI `browser` job
installs Chromium and runs it for real.
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
    base = tmp_path / "browser.db"
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


# Bounded means: never the full-page blowup. We allow the stage to fill its
# track and a few pixels of slack for fractional layout / scrollbar rounding.
_BODY_SLACK = 24


def _viewport_metrics(page) -> dict:
    return page.evaluate(
        """() => {
          const stage = document.querySelector('.graph-stage');
          const canvas = document.querySelector('#graphNetwork canvas');
          const status = document.getElementById('graphStatus');
          const sr = stage.getBoundingClientRect();
          const cr = canvas.getBoundingClientRect();
          const tr = status.getBoundingClientRect();
          return {
            innerHeight: window.innerHeight,
            bodyScrollHeight: document.body.scrollHeight,
            stageTop: sr.top,
            stageHeight: sr.height,
            canvasTop: cr.top,
            canvasBottom: cr.bottom,
            canvasHeight: cr.height,
            statusText: (status.textContent || '').trim(),
            statusVisible: tr.top >= 0 && tr.bottom <= window.innerHeight + 1,
            nodeCount: window.loreGraph.nodeCount,
          };
        }"""
    )


def _assert_bounded(metrics: dict, where: str) -> None:
    inner = metrics["innerHeight"]
    # The whole point: the page must not grow far beyond a single viewport.
    assert metrics["bodyScrollHeight"] <= inner + _BODY_SLACK, f"{where}: body grew to {metrics['bodyScrollHeight']}px"
    # The canvas must fit inside the viewport, not balloon to page height.
    assert metrics["canvasHeight"] <= inner + 1, f"{where}: canvas is {metrics['canvasHeight']}px tall"
    # ...and still be a real, visible stage (not collapsed to nothing).
    assert metrics["canvasHeight"] >= 100, f"{where}: canvas collapsed to {metrics['canvasHeight']}px"
    # The graph must render in the first viewport without scrolling.
    assert 0 <= metrics["stageTop"] < inner, f"{where}: stage starts at y={metrics['stageTop']}"
    assert metrics["canvasBottom"] <= inner + 1, f"{where}: canvas bottom at {metrics['canvasBottom']}"
    # Real rendered content + a visible, non-empty status line. (Overview says
    # "N pages · M links"; ego mode says "... connected within N hops" -- both
    # must be present and on-screen, so just require non-empty text here.)
    assert metrics["nodeCount"] > 0, f"{where}: no nodes rendered"
    assert metrics["statusVisible"], f"{where}: status text not in viewport"
    assert metrics["statusText"], f"{where}: status text is empty"


def test_graph_renders_in_first_viewport(content_dir, tmp_path):
    """The graph showpiece renders bounded + interactive in a real browser."""
    app = _build_app(content_dir, tmp_path)
    with _LiveServer(app, _free_port()) as server, sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except sync_api.Error as exc:  # browser binary not installed
            pytest.skip(f"Playwright Chromium not available: {exc}")
        try:
            # --- Desktop viewport -------------------------------------------------
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            console_errors: list[str] = []

            def _on_console(msg):
                if msg.type == "error":
                    console_errors.append(msg.text)

            page.on("console", _on_console)
            page.on("pageerror", lambda exc: console_errors.append(str(exc)))

            page.goto(f"{server.base_url}/graph", wait_until="domcontentloaded")
            page.wait_for_function("() => window.loreGraph && window.loreGraph.nodeCount > 0", timeout=15000)
            page.wait_for_timeout(300)  # let stabilization/fit settle

            overview = _viewport_metrics(page)
            _assert_bounded(overview, "desktop")
            assert "pages" in overview["statusText"] and "links" in overview["statusText"]
            assert console_errors == [], f"console errors: {console_errors}"

            # --- Ego/tree interaction (same code path as clicking a node) ---------
            first_id = page.evaluate("() => window.loreGraph.nodeIds()[0]")
            assert isinstance(first_id, str) and first_id, f"expected a node id, got {first_id!r}"
            page.evaluate("(id) => window.loreGraph.enterEgo(id)", first_id)
            page.wait_for_function("() => window.loreGraph.mode === 'ego'", timeout=5000)
            ego = page.evaluate(
                """() => ({
                  mode: window.loreGraph.mode,
                  focusId: window.loreGraph.focusId,
                  nodeCount: window.loreGraph.nodeCount,
                  backVisible: !document.getElementById('graphBack').hidden,
                  depthShown: getComputedStyle(document.getElementById('graphDepthWrap')).display !== 'none',
                })"""
            )
            assert ego["mode"] == "ego"
            assert ego["focusId"] == first_id
            assert ego["nodeCount"] >= 1
            assert ego["backVisible"] is True
            assert ego["depthShown"] is True

            # The ego view must also stay inside the viewport.
            _assert_bounded(_viewport_metrics(page), "desktop-ego")

            # --- Back to overview -------------------------------------------------
            page.evaluate("() => window.loreGraph.showOverview()")
            page.wait_for_function("() => window.loreGraph.mode === 'overview'", timeout=5000)
            back = page.evaluate(
                "() => ({ mode: window.loreGraph.mode, backVisible: !document.getElementById('graphBack').hidden })"
            )
            assert back["mode"] == "overview"
            assert back["backVisible"] is False
            assert console_errors == [], f"console errors after interaction: {console_errors}"

            # --- Mobile viewport --------------------------------------------------
            mobile = browser.new_page(viewport={"width": 390, "height": 780})
            mobile.goto(f"{server.base_url}/graph", wait_until="domcontentloaded")
            mobile.wait_for_function("() => window.loreGraph && window.loreGraph.nodeCount > 0", timeout=15000)
            mobile.wait_for_timeout(300)
            _assert_bounded(_viewport_metrics(mobile), "mobile")
        finally:
            browser.close()
