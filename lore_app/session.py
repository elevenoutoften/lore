"""Stdlib-only signed-cookie sessions for the browser UI.

A same-origin HttpOnly + SameSite=strict cookie that authorizes *read-only*
browser access (reader HTML pages and read-only XHR) without pasting a token on
every operator page. Writes stay token-only. Uses hmac/hashlib/secrets only — no
itsdangerous or starlette SessionMiddleware — matching the stdlib crypto already
used in auth.py (secrets.compare_digest) and api_keys.py (hashlib.sha256).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from .constants import VALID_API_KEY_ROLES

SESSION_COOKIE_NAME = "lore_session"
DEFAULT_TTL_SECONDS = 86400


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign_session(
    secret: str,
    actor: str,
    role: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    issued_at: float | None = None,
) -> str:
    """Return a signed ``<payload_b64>.<hmac_hex>`` session token."""
    now = issued_at if issued_at is not None else time.time()
    expiry = int(now) + int(ttl_seconds)
    payload = f"{actor}|{role}|{expiry}"
    payload_b64 = _b64encode(payload.encode("utf-8"))
    mac = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{mac}"


def verify_session(secret: str, token: str) -> tuple[str, str] | None:
    """Validate a session token, returning ``(actor, role)`` or ``None``.

    Returns ``None`` (never raises) on a malformed token, a bad signature, an
    expired token, or an unknown role.
    """
    if not token or "." not in token:
        return None
    payload_b64, _, mac = token.partition(".")
    if not payload_b64 or not mac:
        return None
    expected = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return None
    try:
        payload = _b64decode(payload_b64).decode("utf-8")
        actor, role, expiry_raw = payload.split("|", 2)
        expiry = int(expiry_raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if expiry < int(time.time()):
        return None
    if role not in VALID_API_KEY_ROLES or not actor:
        return None
    return actor, role
