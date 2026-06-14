"""Tests for LLM provider config and client."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from lore_app.config import LoreConfig
from lore_app.llm_provider import (
    DEFAULT_BASE_URL,
    DEFAULT_ESCALATION_MODEL,
    DEFAULT_EXTRACTION_MODEL,
    FallbackLLMClient,
    LLMClient,
    LLMError,
    LLMJsonError,
    LLMProviderConfig,
    LLMUnavailableError,
    NoLlmClient,
    build_llm_client,
)


class TestLLMProviderConfig:
    def test_defaults(self):
        config = LLMProviderConfig(name="test", model="glm-5.1")
        assert config.base_url == DEFAULT_BASE_URL
        assert config.max_tokens == 4096
        assert config.temperature == 0.3
        assert config.max_retries == 3

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("LORE_LLM_MODEL", "glm-5.1")
        monkeypatch.setenv("LORE_LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LORE_LLM_BASE_URL", "https://custom.api/v1")
        config = LLMProviderConfig.from_env("LORE_LLM")
        assert config.model == "glm-5.1"
        assert config.api_key == "sk-test"
        assert config.base_url == "https://custom.api/v1"

    def test_from_env_defaults(self, monkeypatch):
        for key in list(os.environ):
            if key.startswith("LORE_LLM"):
                monkeypatch.delenv(key, raising=False)
        config = LLMProviderConfig.from_env("LORE_LLM")
        assert config.model == DEFAULT_EXTRACTION_MODEL
        assert config.api_key is None
        assert config.base_url == DEFAULT_BASE_URL

    def test_escalation_defaults(self, monkeypatch):
        for key in list(os.environ):
            if key.startswith("LORE_LLM_ESCALATION"):
                monkeypatch.delenv(key, raising=False)
        config = LLMProviderConfig.from_env("LORE_LLM_ESCALATION")
        assert config.model == DEFAULT_ESCALATION_MODEL


class TestLLMClient:
    def test_chat_success(self, monkeypatch):
        config = LLMProviderConfig(name="test", model="test-model", api_key="sk-test")
        client = LLMClient(config)

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}}],
            "usage": {"total_tokens": 10},
            "model": "test-model",
        }
        mock_response.raise_for_status = mock.MagicMock()

        monkeypatch.setattr(client._client, "post", mock.MagicMock(return_value=mock_response))

        result = client.chat([{"role": "user", "content": "Hi"}])
        assert result["content"] == "Hello!"
        client.close()

    def test_extract_json_success(self, monkeypatch):
        config = LLMProviderConfig(name="test", model="test-model", api_key="sk-test")
        client = LLMClient(config)

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"entities": [], "claims": []})}}],
        }
        mock_response.raise_for_status = mock.MagicMock()

        monkeypatch.setattr(client._client, "post", mock.MagicMock(return_value=mock_response))

        result = client.extract_json("You are a extractor.", "Extract from: foo")
        assert result["entities"] == []
        assert result["claims"] == []
        assert result["_lore_meta"] == {"usage": {}, "model": "test-model"}
        client.close()

    def test_extract_json_invalid_json(self, monkeypatch):
        config = LLMProviderConfig(name="test", model="test-model", api_key="sk-test")
        client = LLMClient(config)

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "not valid json"}}],
        }
        mock_response.raise_for_status = mock.MagicMock()

        monkeypatch.setattr(client._client, "post", mock.MagicMock(return_value=mock_response))

        with pytest.raises(LLMJsonError):
            client.extract_json("sys", "user")
        client.close()

    def test_chat_retry_on_connection_error(self, monkeypatch):
        config = LLMProviderConfig(name="test", model="test-model", api_key="sk-test", max_retries=2)
        client = LLMClient(config)

        from httpx import ConnectError

        call_count = 0

        def mock_post(url, json=None):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectError("Connection refused")
            mock_resp = mock.MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "retried!"}}],
            }
            mock_resp.raise_for_status = mock.MagicMock()
            return mock_resp

        monkeypatch.setattr(client._client, "post", mock_post)
        monkeypatch.setattr("time.sleep", mock.MagicMock())

        result = client.chat([{"role": "user", "content": "hi"}])
        assert result["content"] == "retried!"
        assert call_count == 2
        client.close()

    def test_chat_permanent_failure(self, monkeypatch):
        config = LLMProviderConfig(name="test", model="test-model", api_key="sk-test")
        client = LLMClient(config)

        from httpx import HTTPStatusError

        mock_response = mock.MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        def mock_post(url, json=None):
            raise HTTPStatusError("401", request=mock.MagicMock(), response=mock_response)

        monkeypatch.setattr(client._client, "post", mock_post)

        with pytest.raises(LLMError, match="401"):
            client.chat([{"role": "user", "content": "hi"}])
        client.close()


class TestFallbackLLMClient:
    def test_primary_success(self):
        primary = mock.MagicMock(spec=LLMClient)
        primary.extract_json.return_value = {"entities": [{"name": "test"}]}

        client = FallbackLLMClient(primary=primary)
        result = client.extract_json("sys", "user")
        assert result == {"entities": [{"name": "test"}]}
        assert primary.extract_json.call_count == 1

    def test_escalation_on_primary_failure(self):
        primary = mock.MagicMock(spec=LLMClient)
        primary.extract_json.side_effect = LLMError("Primary failed")

        escalation = mock.MagicMock(spec=LLMClient)
        escalation.extract_json.return_value = {"entities": [{"name": "escalated"}]}

        client = FallbackLLMClient(primary=primary, escalation=escalation)
        result = client.extract_json("sys", "user")
        assert result == {"entities": [{"name": "escalated"}]}
        assert escalation.extract_json.call_count == 1

    def test_fallback_fn_on_all_failure(self):
        primary = mock.MagicMock(spec=LLMClient)
        primary.extract_json.side_effect = LLMError("Primary failed")

        escalation = mock.MagicMock(spec=LLMClient)
        escalation.extract_json.side_effect = LLMError("Escalation failed")

        fallback = mock.MagicMock(return_value={"entities": [{"name": "fallback"}]})

        client = FallbackLLMClient(primary=primary, escalation=escalation, fallback_fn=fallback)
        result = client.extract_json("sys", "user")
        assert result == {"entities": [{"name": "fallback"}]}

    def test_raises_when_no_fallback(self):
        primary = mock.MagicMock(spec=LLMClient)
        primary.extract_json.side_effect = LLMError("Primary failed")

        client = FallbackLLMClient(primary=primary, escalation=None, fallback_fn=None)
        with pytest.raises(LLMError, match="no deterministic fallback"):
            client.extract_json("sys", "user")


class TestBuildLLMClient:
    def test_default_lore_config_is_safe(self):
        config = LoreConfig()
        assert config.llm_provider == "none"
        assert config.llm_model == ""
        assert config.llm_base_url == ""

    def test_build_with_env(self, monkeypatch):
        monkeypatch.setenv("LORE_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LORE_LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LORE_LLM_MODEL", "minimax-m3")
        client = build_llm_client()
        assert isinstance(client, FallbackLLMClient)
        assert client.primary.config.model == "minimax-m3"
        assert client.primary.config.api_key == "sk-test"
        client.close()

    def test_build_without_key(self, monkeypatch):
        for key in list(os.environ):
            if key.startswith("LORE_LLM"):
                monkeypatch.delenv(key, raising=False)
        client = build_llm_client()
        assert isinstance(client, NoLlmClient)
        client.close()

    def test_no_llm_client_extract_json_raises(self):
        client = NoLlmClient()
        with pytest.raises(LLMUnavailableError, match="provider is 'none'"):
            client.extract_json("sys", "user")

    def test_no_llm_client_chat_raises(self):
        client = NoLlmClient()
        with pytest.raises(LLMUnavailableError, match="provider is 'none'"):
            client.chat([{"role": "user", "content": "hi"}])

    def test_no_llm_client_close_is_noop(self):
        NoLlmClient().close()

    def test_build_from_default_lore_config_returns_no_llm_client(self):
        client = build_llm_client(config=LoreConfig())
        assert isinstance(client, NoLlmClient)
        client.close()

    def test_build_from_explicit_provider_returns_real_client(self):
        config = LoreConfig()
        config.llm_provider = "ollama"
        config.llm_model = "minimax-m3"
        config.llm_base_url = DEFAULT_BASE_URL
        config.llm_api_key = "sk-test"
        config.llm_timeout_seconds = 12.5
        config.llm_max_retries = 7
        config.llm_escalation_api_key = "sk-escalation"

        client = build_llm_client(config=config)

        assert isinstance(client, FallbackLLMClient)
        assert client.primary.config.model == "minimax-m3"
        assert client.primary.config.timeout_seconds == 12.5
        assert client.primary.config.max_retries == 7
        assert client.escalation is not None
        assert client.escalation.config.timeout_seconds == 12.5
        assert client.escalation.config.max_retries == 7
        client.close()

    def test_escalation_reuses_primary_key_via_lore_config(self):
        config = LoreConfig()
        config.llm_provider = "ollama"
        config.llm_model = "glm-5.1"
        config.llm_api_key = "sk-test"
        config.llm_escalation_api_key = None

        client = build_llm_client(config=config)

        assert isinstance(client, FallbackLLMClient)
        assert client.escalation is not None
        assert client.escalation.config.api_key == "sk-test"
        client.close()

    def test_primary_model_defaults_when_unset(self):
        config = LoreConfig()
        config.llm_provider = "ollama"
        config.llm_api_key = "sk-test"
        config.llm_model = ""

        client = build_llm_client(config=config)

        assert isinstance(client, FallbackLLMClient)
        assert client.primary.config.model == "glm-5.1"
        client.close()
