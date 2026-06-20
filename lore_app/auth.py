"""Pluggable auth middleware for Lore."""

from __future__ import annotations

import base64
import ipaddress
import secrets
from typing import TYPE_CHECKING, Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from .api_keys import LoreApiKeyStore


class AuthMiddleware(BaseHTTPMiddleware):
    """Auth middleware supporting none, bearer, basic, and Lore API key modes."""

    def __init__(
        self,
        app: Any,
        mode: str = "none",
        secret: str = "",
        api_key_store: LoreApiKeyStore | None = None,
        trusted_proxy_auth: bool = False,
        trusted_proxy_cidrs: list[str] | None = None,
        trusted_proxy_secret: str = "",
    ) -> None:
        if mode in ("bearer", "basic") and (not secret or not secret.strip()):
            raise ValueError(
                f"Auth mode '{mode}' requires a non-empty secret. "
                f"Set LORE_AUTH_SECRET or switch to a different auth mode."
            )
        super().__init__(app)
        self.mode = mode
        self.secret = secret
        self.api_key_store = api_key_store
        self.trusted_proxy_auth = trusted_proxy_auth
        self.trusted_proxy_secret = trusted_proxy_secret
        self.trusted_proxy_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for cidr in trusted_proxy_cidrs or []:
            try:
                self.trusted_proxy_networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError:
                # Skip malformed CIDRs rather than failing startup; an empty
                # allowlist (plus no secret) just means no proxy promotion.
                continue

    @staticmethod
    def _is_public_path(path: str) -> bool:
        """Public, unauthenticated paths.

        Uses exact/segment-boundary matching, never a bare prefix: a substring
        prefix test would let page ids like ``staticsecret`` or ``healthznotes``
        (served by the catch-all reader route) bypass auth entirely.
        """
        return path in ("/healthz", "/healthz/config", "/metrics", "/static") or path.startswith("/static/")

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if self.mode == "none":
            return await call_next(request)

        path = request.url.path
        if self._is_public_path(path):
            return await call_next(request)

        if self.mode == "bearer":
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer ") and self._check_token(auth[7:]):
                request.state.lore_actor = "bearer"
                # The holder of the single global secret is the trusted operator, so
                # grant admin: this is the only identity in bearer/basic mode, and
                # without it the documented /api-keys + /settings bootstrap dead-ends.
                request.state.lore_role = "admin"
                return await call_next(request)
            proxy_response = await self._trusted_proxy_response(request, call_next)
            if proxy_response is not None:
                return proxy_response
            return Response(status_code=401, content="Unauthorized", headers={"WWW-Authenticate": "Bearer"})

        if self.mode == "basic":
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
                except ValueError:
                    decoded = ""
                if secrets.compare_digest(decoded, self.secret):
                    request.state.lore_actor = decoded.split(":", 1)[0] or "basic"
                    # Holder of the single global secret is the trusted admin operator.
                    request.state.lore_role = "admin"
                    return await call_next(request)
            proxy_response = await self._trusted_proxy_response(request, call_next)
            if proxy_response is not None:
                return proxy_response
            return Response(status_code=401, content="Unauthorized", headers={"WWW-Authenticate": "Basic"})

        if self.mode == "api_key":
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                api_key = self.api_key_store.verify_token(auth[7:].strip()) if self.api_key_store else None
                if api_key is not None:
                    request.state.lore_actor = api_key.name
                    request.state.lore_role = api_key.role
                    if api_key.role == "reader" and self._is_write_request(request):
                        return Response(status_code=403, content="Forbidden")
                    return await call_next(request)

            proxy_response = await self._trusted_proxy_response(request, call_next)
            if proxy_response is not None:
                return proxy_response
            return Response(status_code=401, content="Unauthorized", headers={"WWW-Authenticate": "Bearer"})

        return await call_next(request)

    def _check_token(self, token: str) -> bool:
        return secrets.compare_digest(token, self.secret)

    def _is_write_request(self, request: Request) -> bool:
        method = request.method.upper()
        if method in {"PUT", "PATCH", "DELETE"}:
            return True
        if method != "POST":
            return False
        path = request.url.path
        return path.startswith("/api/")

    async def _trusted_proxy_response(self, request: Request, call_next: Any) -> Response | None:
        if not self.trusted_proxy_auth:
            return None
        actor, role = self._resolve_trusted_proxy(request)
        if actor is None:
            return None
        request.state.lore_actor = actor
        request.state.lore_role = role
        if role == "reader" and self._is_write_request(request):
            return Response(status_code=403, content="Forbidden")
        return await call_next(request)

    def _proxy_origin_trusted(self, request: Request) -> bool:
        """Whether this request is allowed to supply trusted-proxy identity headers.

        Trust requires proof that the request actually came from the fronting
        proxy: either a shared secret header (X-Lore-Proxy-Secret) or a source IP
        inside a configured CIDR allowlist. With neither configured we fail closed
        and ignore identity headers entirely — the classic ``X-Axis-Admin: 1``
        spoof from an arbitrary client is rejected.
        """
        if self.trusted_proxy_secret:
            provided = request.headers.get("X-Lore-Proxy-Secret", "")
            if provided and secrets.compare_digest(provided, self.trusted_proxy_secret):
                return True
        if self.trusted_proxy_networks and request.client is not None:
            try:
                addr = ipaddress.ip_address(request.client.host)
            except ValueError:
                # Non-IP hosts (e.g. Starlette's 'testclient') are never trusted.
                return False
            return any(addr in network for network in self.trusted_proxy_networks)
        return False

    def _resolve_trusted_proxy(self, request: Request) -> tuple[str | None, str]:
        """Resolve actor and role from trusted proxy identity headers."""
        if not self._proxy_origin_trusted(request):
            return None, ""
        admin_header = request.headers.get("X-Axis-Admin", "").strip()
        agent_header = request.headers.get("X-Lore-Agent", "").strip()
        user_header = request.headers.get("X-Axis-User", "").strip()
        actor_header = request.headers.get("X-Lore-Actor", "").strip()

        actor = agent_header or user_header or actor_header or None
        if actor is None:
            return None, ""

        role = "admin" if admin_header == "1" else "reader"
        return actor, role
