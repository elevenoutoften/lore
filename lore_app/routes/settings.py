from __future__ import annotations

# ruff: noqa: B008
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from ..config import LoreConfig, merged_llm_config
from ..deps import get_api_key_store, get_config, get_settings_store, get_templates
from ..route_utils import template_context
from ..schemas import LlmSettingsResponse, LlmSettingsUpdate
from ..settings_store import (
    LLM_SETTINGS_KEYS,
    SECRET_SETTINGS_KEYS,
    SETTINGS_LLM_API_KEY,
    SETTINGS_LLM_BASE_URL,
    SETTINGS_LLM_ESCALATION_API_KEY,
    SETTINGS_LLM_ESCALATION_MODEL,
    SETTINGS_LLM_MAX_RETRIES,
    SETTINGS_LLM_MAX_TOKENS,
    SETTINGS_LLM_MODEL,
    SETTINGS_LLM_PROVIDER,
    SETTINGS_LLM_TEMPERATURE,
    SETTINGS_LLM_TIMEOUT_SECONDS,
    SettingsStore,
    mask_secret,
)
from .api_keys import _extract_bearer_token, require_lore_key_admin

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from ..api_keys import LoreApiKeyStore

logger = logging.getLogger(__name__)
settings_router = APIRouter(tags=["settings"])


@settings_router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
):
    return templates.TemplateResponse(request, "settings.html", template_context(request, title="Settings"))


def require_lore_key(
    request: Request,
    authorization: str | None = Header(default=None),
    store: LoreApiKeyStore = Depends(get_api_key_store),
) -> None:
    # Auth disabled (default local config) has no boundary: let the local operator
    # read settings so the /settings page works out of the box.
    if getattr(request.app.state.config, "auth_mode", None) == "none":
        return
    state_role = str(getattr(request.state, "lore_role", "") or "").strip()
    if state_role in {"admin", "writer", "reader"}:
        return

    token = _extract_bearer_token(authorization)
    if token:
        api_key = store.verify_token(token)
        if api_key is not None:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lore API key required.")

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.")


@settings_router.get("/api/settings/llm", response_model=LlmSettingsResponse)
def api_get_llm_settings(
    _key: None = Depends(require_lore_key),
    config: LoreConfig = Depends(get_config),
    settings_store: SettingsStore = Depends(get_settings_store),
) -> LlmSettingsResponse:
    return _llm_settings_response(config, settings_store)


@settings_router.put("/api/settings/llm", response_model=LlmSettingsResponse)
def api_update_llm_settings(
    payload: LlmSettingsUpdate,
    request: Request,
    _admin: None = Depends(require_lore_key_admin),
    config: LoreConfig = Depends(get_config),
    settings_store: SettingsStore = Depends(get_settings_store),
) -> LlmSettingsResponse:
    for field_name, key in _FIELD_TO_KEY.items():
        value = getattr(payload, field_name)
        if value is not None:
            settings_store.set(key, str(value), secret=key in SECRET_SETTINGS_KEYS)

    logger.info("LLM settings updated")
    from ..main import rebuild_llm_client

    rebuild_llm_client(request.app)
    return _llm_settings_response(config, settings_store)


@settings_router.delete("/api/settings/llm", response_model=LlmSettingsResponse)
def api_delete_llm_settings(
    request: Request,
    _admin: None = Depends(require_lore_key_admin),
    config: LoreConfig = Depends(get_config),
    settings_store: SettingsStore = Depends(get_settings_store),
) -> LlmSettingsResponse:
    for key in LLM_SETTINGS_KEYS:
        settings_store.delete(key)

    logger.info("LLM settings cleared")
    from ..main import rebuild_llm_client

    rebuild_llm_client(request.app)
    return _llm_settings_response(config, settings_store)


_FIELD_TO_KEY: dict[str, str] = {
    "provider": SETTINGS_LLM_PROVIDER,
    "model": SETTINGS_LLM_MODEL,
    "base_url": SETTINGS_LLM_BASE_URL,
    "api_key": SETTINGS_LLM_API_KEY,
    "escalation_model": SETTINGS_LLM_ESCALATION_MODEL,
    "escalation_api_key": SETTINGS_LLM_ESCALATION_API_KEY,
    "max_tokens": SETTINGS_LLM_MAX_TOKENS,
    "temperature": SETTINGS_LLM_TEMPERATURE,
    "timeout_seconds": SETTINGS_LLM_TIMEOUT_SECONDS,
    "max_retries": SETTINGS_LLM_MAX_RETRIES,
}


def _llm_settings_response(config: LoreConfig, settings_store: SettingsStore) -> LlmSettingsResponse:
    provider_config = merged_llm_config(config, settings_store)
    return LlmSettingsResponse(
        provider=provider_config.name,
        model=provider_config.model,
        base_url=provider_config.base_url or "",
        api_key_configured=bool(provider_config.api_key),
        api_key_hint=mask_secret(provider_config.api_key) or "",
        escalation_model=provider_config.escalation_model or "",
        escalation_api_key_configured=bool(provider_config.escalation_api_key),
        escalation_api_key_hint=mask_secret(provider_config.escalation_api_key) or "",
        max_tokens=provider_config.max_tokens,
        temperature=provider_config.temperature,
        timeout_seconds=provider_config.timeout_seconds,
        max_retries=provider_config.max_retries,
    )
