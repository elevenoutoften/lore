from __future__ import annotations

import re

from lore_app.security import RateLimiter, sanitize_content, sanitize_page_id


def test_path_traversal_is_blocked(client):
    response = client.put("/api/pages/projects/%2e%2e/secrets", json={"content": "# Secret"})
    assert response.status_code == 422

    try:
        sanitize_page_id("projects/../secrets")
    except ValueError as exc:
        assert "'..'" in str(exc)
    else:
        raise AssertionError("Expected path traversal to be blocked")


def test_null_bytes_blocked(client):
    response = client.put("/api/pages/projects/%00secret", json={"content": "# Secret"})
    assert response.status_code == 422

    try:
        sanitize_page_id("projects/\x00secret")
    except ValueError as exc:
        assert "null bytes" in str(exc)
    else:
        raise AssertionError("Expected null byte page ID to be blocked")


def test_hidden_files_blocked(client):
    response = client.put("/api/pages/.hidden/page", json={"content": "# Hidden"})
    assert response.status_code == 422

    try:
        sanitize_page_id("projects/.hidden")
    except ValueError as exc:
        assert "start with '.'" in str(exc)
    else:
        raise AssertionError("Expected hidden path segment to be blocked")


def test_sanitize_page_id_returns_normalized_path():
    assert sanitize_page_id(" projects\\example-project ") == "projects/example-project"


def test_content_length_limit():
    try:
        sanitize_content("abcd", max_length=3)
    except ValueError as exc:
        assert "Content exceeds max length" in str(exc)
    else:
        raise AssertionError("Expected oversized content to be blocked")


def test_rate_limiting_works():
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    assert limiter.check("127.0.0.1") is True
    assert limiter.check("127.0.0.1") is True
    assert limiter.check("127.0.0.1") is False
    assert limiter.check("10.0.0.2") is True


def test_rate_limiter_sweeps_expired_keys():
    limiter = RateLimiter(max_requests=1, window_seconds=0)
    for index in range(10_002):
        assert limiter.check(f"client-{index}") is True

    assert limiter.check("fresh-client") is True
    assert len(limiter._requests) < 10_000


def test_write_rate_limiting_returns_429(client):
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    content = "# Rate Limit\n"

    first = client.put("/api/pages/security/rate-limit-1", json={"content": content})
    assert first.status_code == 200

    second = client.put("/api/pages/security/rate-limit-2", json={"content": content})
    assert second.status_code == 429


def test_rate_limit_429_includes_security_headers(client):
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    content = "# Rate Limit\n"

    first = client.put("/api/pages/security/headers-1", json={"content": content})
    assert first.status_code == 200

    second = client.put("/api/pages/security/headers-2", json={"content": content})
    assert second.status_code == 429
    assert second.headers["X-Content-Type-Options"] == "nosniff"
    assert second.headers["X-Frame-Options"] == "DENY"
    assert second.headers["X-XSS-Protection"] == "1; mode=block"
    assert second.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert re.fullmatch(
        r"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-[^']+'",
        second.headers["Content-Security-Policy"],
    )


def test_security_headers_present_on_responses(client):
    response = client.get("/api/pages")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert re.fullmatch(
        r"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-[^']+'",
        response.headers["Content-Security-Policy"],
    )


def test_embed_route_is_framable(client):
    """The /embed widget must be loadable in an iframe (no XFO: DENY; frame-ancestors set)."""
    response = client.get("/embed", params={"mode": "search"})
    assert response.status_code == 200
    assert response.headers.get("X-Frame-Options") != "DENY"
    assert "frame-ancestors" in response.headers["Content-Security-Policy"]


def test_template_scripts_use_csp_nonce(client):
    """SSR operator pages embed inline scripts that must carry the per-request CSP
    nonce (the lore2 reader/index pages instead load an external app.js)."""
    response = client.get("/settings")
    assert response.status_code == 200
    nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)
    assert f'<script nonce="{nonce}">' in response.text


def test_spa_shell_loads_external_script_without_inline(client):
    """The lore2 SPA shell loads app.js as an external script and embeds no
    un-nonced inline script, so the nonce-gated script-src (no 'unsafe-inline')
    would block an injected inline script."""
    response = client.get("/")
    assert response.status_code == 200
    assert '<script src="/static/lore2/app.js' in response.text
    # No inline <script> without a src or nonce — anything injected would be CSP-blocked.
    assert re.search(r"<script(?![^>]*\bsrc=)(?![^>]*\bnonce=)", response.text) is None
    csp = response.headers["Content-Security-Policy"]
    script_src = csp.split("script-src", 1)[1]
    assert "'nonce-" in script_src
    assert "'unsafe-inline'" not in script_src
