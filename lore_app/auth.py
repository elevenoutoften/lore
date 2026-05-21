"""Pluggable auth middleware for Lore."""
from __future__ import annotations

import base64
import secrets
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .api_keys import LoreApiKeyStore


class AuthMiddleware(BaseHTTPMiddleware):
    """Auth middleware supporting none, bearer, basic, and Lore API key modes."""

    def __init__(
        self,
        app: Any,
        mode: str = "none",
        secret: str = "",
        api_key_store: LoreApiKeyStore | None = None,
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
        self.public_paths = {"/healthz", "/static"}

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if self.mode == "none":
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(public_path) for public_path in self.public_paths):
            return await call_next(request)

        if self.mode == "bearer":
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer ") and self._check_token(auth[7:]):
                request.state.lore_actor = "bearer"
                return await call_next(request)
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
                    return await call_next(request)
            return Response(status_code=401, content="Unauthorized", headers={"WWW-Authenticate": "Basic"})

        if self.mode == "api_key":
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                api_key = self.api_key_store.verify_token(auth[7:].strip()) if self.api_key_store else None
                if api_key is None:
                    return Response(status_code=401, content="Unauthorized", headers={"WWW-Authenticate": "Bearer"})
                request.state.lore_actor = api_key.name
                request.state.lore_role = api_key.role
                if api_key.role == "reader" and self._is_write_request(request):
                    return Response(status_code=403, content="Forbidden")
                return await call_next(request)

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
        return path.startswith("/api/") or path.startswith("/mcp")
