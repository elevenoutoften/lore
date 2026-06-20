from __future__ import annotations

# ruff: noqa: B008
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from ..deps import get_api_key_store, get_templates
from ..route_utils import none_mode_local_operator, template_context
from ..schemas import LoginRequest, LoreApiKeyCreate, LoreApiKeyCreateResponse, LoreApiKeyResponse
from ..session import DEFAULT_TTL_SECONDS, SESSION_COOKIE_NAME, sign_session

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from ..api_keys import LoreApiKey, LoreApiKeyStore

router = APIRouter(tags=["api-keys"])


def require_lore_key_admin(
    request: Request,
    authorization: str | None = Header(default=None),
    store: LoreApiKeyStore = Depends(get_api_key_store),
) -> None:
    # When auth is disabled (the default local/dev config) there is no auth
    # boundary, so the local operator is treated as admin. This bypass is gated on
    # the request actually being local: under LORE_ALLOW_INSECURE_BIND a non-loopback
    # auth='none' bind must not hand admin key-minting to arbitrary network clients.
    # (Use `lore key create` for non-local bootstrap.)
    if none_mode_local_operator(request):
        return
    state_role = str(getattr(request.state, "lore_role", "") or "").strip()
    if state_role == "admin":
        return

    token = _extract_bearer_token(authorization)
    if token:
        api_key = store.verify_token(token)
        if api_key is not None and api_key.role == "admin":
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin API key required.")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Admin authentication is required. Send an admin Lore key as a bearer token, or bootstrap one "
            "with `lore key create --role admin`. On a default local install (LORE_AUTH_MODE=none) the keys "
            "and settings pages are open to the loopback operator."
        ),
    )


@router.get("/api-keys", response_class=HTMLResponse)
def api_key_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
):
    return templates.TemplateResponse(
        request,
        "api_keys.html",
        template_context(request, title="API Keys"),
    )


@router.get("/api/api-keys", response_model=list[LoreApiKeyResponse])
def api_list_api_keys(
    _admin: None = Depends(require_lore_key_admin),
    store: LoreApiKeyStore = Depends(get_api_key_store),
):
    return [_serialize_key(api_key) for api_key in store.list_keys()]


@router.post("/api/api-keys", response_model=LoreApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def api_create_api_key(
    payload: LoreApiKeyCreate,
    _admin: None = Depends(require_lore_key_admin),
    store: LoreApiKeyStore = Depends(get_api_key_store),
):
    try:
        api_key, raw_key = store.create_key(
            name=payload.name,
            description=payload.description,
            role=payload.role.value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = _serialize_key(api_key).model_dump()
    response["api_key"] = raw_key
    return LoreApiKeyCreateResponse(**response)


@router.post("/api/api-keys/{api_key_id}/revoke", response_model=LoreApiKeyResponse)
def api_revoke_api_key(
    api_key_id: str,
    _admin: None = Depends(require_lore_key_admin),
    store: LoreApiKeyStore = Depends(get_api_key_store),
):
    api_key = store.revoke_key(api_key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found.")
    return _serialize_key(api_key)


@router.post("/api/login")
def api_login(
    payload: LoginRequest,
    request: Request,
    store: LoreApiKeyStore = Depends(get_api_key_store),
) -> JSONResponse:
    """Exchange a Lore API key for a same-origin HttpOnly session cookie.

    The cookie authorizes read-only browser access (reader HTML + read-only XHR);
    write endpoints stay token-only. Public so a browser can bootstrap a session.
    """
    api_key = store.verify_token(payload.api_key.strip())
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")
    token = sign_session(request.app.state.session_secret, actor=api_key.name, role=api_key.role)
    response = JSONResponse({"actor": api_key.name, "role": api_key.role})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=DEFAULT_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@router.post("/api/logout")
def api_logout() -> JSONResponse:
    """Clear the browser session cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.strip().partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _serialize_key(api_key: LoreApiKey) -> LoreApiKeyResponse:
    return LoreApiKeyResponse(
        id=api_key.id,
        name=api_key.name,
        description=api_key.description,
        role=api_key.role,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        revoked_at=api_key.revoked_at,
    )
