"""LLM provider configuration and client for extraction and other LLM tasks.

Supports OpenAI-compatible APIs (GLM-5.1 / MiniMax-M3 on Ollama Cloud, and any
other OpenAI-compatible endpoint) with:
- Configurable model, base_url, api_key
- JSON-object mode for guaranteed valid JSON output
- Retry with exponential backoff
- Deterministic fallback on failure
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from httpx import Client, ConnectError, HTTPStatusError, TimeoutException

if TYPE_CHECKING:
    from .config import LoreConfig

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_MODEL = "glm-5.1"
DEFAULT_ESCALATION_MODEL = "minimax-m3"
DEFAULT_BASE_URL = "https://ollama.com/v1"


@dataclass(frozen=True)
class LLMProviderConfig:
    """Configuration for a single LLM provider endpoint."""

    name: str
    model: str
    base_url: str | None = DEFAULT_BASE_URL
    api_key: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.3
    timeout_seconds: float = 60.0
    max_retries: int = 3
    escalation_model: str | None = None
    escalation_api_key: str | None = None

    @classmethod
    def from_env(cls, prefix: str = "LORE_LLM") -> LLMProviderConfig:
        """Load provider configuration from environment variables."""

        default_model = DEFAULT_ESCALATION_MODEL if prefix.endswith("_ESCALATION") else DEFAULT_EXTRACTION_MODEL
        return cls(
            name=os.environ.get(f"{prefix}_PROVIDER", "none"),
            model=os.environ.get(f"{prefix}_MODEL", default_model),
            base_url=os.environ.get(f"{prefix}_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get(f"{prefix}_API_KEY"),
            max_tokens=int(os.environ.get(f"{prefix}_MAX_TOKENS", "4096")),
            temperature=float(os.environ.get(f"{prefix}_TEMPERATURE", "0.3")),
            timeout_seconds=float(os.environ.get(f"{prefix}_TIMEOUT", "60")),
            max_retries=int(os.environ.get(f"{prefix}_MAX_RETRIES", "3")),
        )


class LLMClient:
    """Synchronous OpenAI-compatible LLM client with retry and JSON-object mode."""

    def __init__(self, config: LLMProviderConfig):
        self.config = config
        self._client = Client(
            base_url=config.base_url or DEFAULT_BASE_URL,
            headers=self._build_headers(),
            timeout=config.timeout_seconds,
        )

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat completion request with retry."""

        payload: dict[str, Any] = {
            "model": model or self.config.model or DEFAULT_EXTRACTION_MODEL,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]

                if response_format and response_format.get("type") == "json_object":
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError as exc:
                        raise LLMJsonError(f"LLM returned invalid JSON: {exc}\nContent: {content[:200]}") from exc
                    if isinstance(parsed, dict):
                        parsed["_lore_meta"] = {
                            "usage": data.get("usage", {}),
                            "model": data.get("model") or payload["model"],
                        }
                    return parsed

                return {
                    "content": content,
                    "usage": data.get("usage", {}),
                    "model": data.get("model", ""),
                }
            except (ConnectError, TimeoutException) as exc:
                last_error = exc
                wait = 0.5 * (2**attempt)
                logger.warning(
                    "LLM request failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt + 1,
                    self.config.max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
            except HTTPStatusError as exc:
                if exc.response.status_code in (429, 500, 502, 503):
                    last_error = exc
                    wait = 0.5 * (2**attempt)
                    logger.warning(
                        "LLM request failed with %d (attempt %d/%d). Retrying in %.1fs",
                        exc.response.status_code,
                        attempt + 1,
                        self.config.max_retries,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    raise LLMError(
                        f"LLM request failed with status {exc.response.status_code}: {exc.response.text}"
                    ) from exc
            except LLMJsonError:
                raise

        raise LLMError(f"LLM request failed after {self.config.max_retries} retries: {last_error}")

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Run a JSON-object extraction request and return the parsed object."""

        return self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
            model=model,
        )

    def close(self) -> None:
        self._client.close()


class LLMError(Exception):
    """Permanent LLM request failure."""


class LLMUnavailableError(Exception):
    """Raised when LLM features are requested but no provider is configured."""


class LLMJsonError(LLMError):
    """LLM returned invalid JSON in json_object mode."""


class NoLlmClient:
    """No-op LLM client returned when no provider is configured."""

    def extract_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise LLMUnavailableError(
            "LLM provider is 'none'; no extraction available. "
            "Set LORE_LLM_PROVIDER and LORE_LLM_API_KEY to enable LLM features."
        )

    def chat(self, *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        raise LLMUnavailableError(
            "LLM provider is 'none'; no chat available. "
            "Set LORE_LLM_PROVIDER and LORE_LLM_API_KEY to enable LLM features."
        )

    def close(self) -> None:
        pass


class FallbackLLMClient:
    """LLM client with deterministic fallback on failure."""

    def __init__(
        self,
        primary: LLMClient,
        escalation: LLMClient | None = None,
        fallback_fn: Any | None = None,
    ):
        self.primary = primary
        self.escalation = escalation
        self._fallback_fn = fallback_fn

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Try primary, then escalation, then deterministic fallback."""

        try:
            return self.primary.extract_json(
                system_prompt,
                user_prompt,
                temperature=temperature,
                model=model,
            )
        except LLMError as exc:
            logger.warning("Primary LLM extraction failed: %s", exc)

        if self.escalation:
            try:
                return self.escalation.extract_json(system_prompt, user_prompt)
            except LLMError as exc:
                logger.warning("Escalation LLM extraction failed: %s", exc)

        if self._fallback_fn is not None:
            logger.info("Using deterministic fallback for extraction")
            return self._fallback_fn(user_prompt)

        raise LLMError("All LLM providers failed and no deterministic fallback configured")

    def close(self) -> None:
        self.primary.close()
        if self.escalation:
            self.escalation.close()


def _primary_config_from_lore_config(config: LoreConfig) -> LLMProviderConfig:
    return LLMProviderConfig(
        name=config.llm_provider,
        model=config.llm_model or DEFAULT_EXTRACTION_MODEL,
        base_url=config.llm_base_url or None,
        api_key=config.llm_api_key,
        max_tokens=config.llm_max_tokens,
        temperature=config.llm_temperature,
        timeout_seconds=config.llm_timeout_seconds,
        max_retries=config.llm_max_retries,
    )


def _escalation_config_from_lore_config(config: LoreConfig) -> LLMProviderConfig:
    return LLMProviderConfig(
        name=config.llm_provider,
        model=config.llm_escalation_model or DEFAULT_ESCALATION_MODEL,
        base_url=config.llm_base_url or None,
        # Reuse the primary key for escalation when no separate key is set, so the
        # CLI path escalates identically to the server path (build_llm_client_from_config).
        api_key=config.llm_escalation_api_key or config.llm_api_key,
        max_tokens=config.llm_max_tokens,
        temperature=config.llm_temperature,
        timeout_seconds=config.llm_timeout_seconds,
        max_retries=config.llm_max_retries,
    )


def build_llm_client(
    config: LoreConfig | None = None,
    fallback_fn: Any | None = None,
) -> FallbackLLMClient | NoLlmClient:
    """Build a fallback-capable LLM client from LoreConfig or environment variables."""

    primary_config = _primary_config_from_lore_config(config) if config else LLMProviderConfig.from_env("LORE_LLM")
    if config:
        escalation_config = _escalation_config_from_lore_config(config)
    else:
        escalation_config = LLMProviderConfig.from_env("LORE_LLM_ESCALATION")
    if not escalation_config.api_key:
        escalation_config = None

    return build_llm_client_from_config(
        primary_config,
        escalation_config=escalation_config,
        fallback_fn=fallback_fn,
    )


def build_llm_client_from_config(
    provider_config: LLMProviderConfig,
    *,
    escalation_config: LLMProviderConfig | None = None,
    fallback_fn: Any | None = None,
) -> FallbackLLMClient | LLMClient | NoLlmClient:
    """Build an LLM client from a resolved provider config."""

    if provider_config.name == "none" or not provider_config.name:
        return NoLlmClient()

    primary = LLMClient(provider_config)
    if escalation_config is None and provider_config.escalation_model:
        escalation_config = LLMProviderConfig(
            name=f"{provider_config.name}-escalation",
            model=provider_config.escalation_model,
            base_url=provider_config.base_url,
            api_key=provider_config.escalation_api_key or provider_config.api_key,
            max_tokens=provider_config.max_tokens,
            temperature=provider_config.temperature,
            timeout_seconds=provider_config.timeout_seconds,
            max_retries=provider_config.max_retries,
        )

    if escalation_config and escalation_config.api_key:
        return FallbackLLMClient(primary=primary, escalation=LLMClient(escalation_config), fallback_fn=fallback_fn)
    return FallbackLLMClient(primary=primary, escalation=None, fallback_fn=fallback_fn)
