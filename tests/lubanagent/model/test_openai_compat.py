"""OpenAI 兼容接入: PROVIDERS 预填、密钥只来自 env、base_url 回退。"""

import pytest
from langchain_openai import ChatOpenAI

from lubanagent.core import ErrorCode, LubanError, ModelDefinition
from lubanagent.model import (
    PROVIDERS,
    create_openai_compatible,
    openai_compat_definition,
)


def test_providers_content() -> None:
    assert PROVIDERS == {
        "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"},
        "qwen": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_env": "DASHSCOPE_API_KEY",
        },
        "glm": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key_env": "ZHIPUAI_API_KEY",
        },
        "vllm": {},
        "ollama": {"base_url": "http://localhost:11434/v1"},
    }


def test_deepseek_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    definition = openai_compat_definition(
        key="ds-chat", provider="deepseek", model_name="deepseek-chat"
    )
    assert definition.factory == "openai_compat"
    assert definition.api_key_env == "DEEPSEEK_API_KEY"
    model = create_openai_compatible(definition)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "deepseek-chat"
    assert model.openai_api_base == "https://api.deepseek.com/v1"
    assert model.openai_api_key.get_secret_value() == "sk-test-deepseek"


def test_missing_api_key_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    definition = openai_compat_definition(
        key="ds-chat", provider="deepseek", model_name="deepseek-chat"
    )
    with pytest.raises(LubanError) as exc_info:
        create_openai_compatible(definition)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details == {"api_key_env": "DEEPSEEK_API_KEY"}


def test_ollama_no_key_uses_empty() -> None:
    definition = openai_compat_definition(key="local", provider="ollama", model_name="qwen3:8b")
    assert definition.api_key_env is None
    model = create_openai_compatible(definition)
    assert model.openai_api_base == "http://localhost:11434/v1"
    assert model.openai_api_key.get_secret_value() == "EMPTY"


def test_vllm_base_url_from_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = openai_compat_definition(
        key="vllm-qwen",
        provider="vllm",
        model_name="qwen3",
        base_url="http://gpu-box:8000/v1",
        params={"temperature": 0.1},
    )
    model = create_openai_compatible(definition)
    assert model.openai_api_base == "http://gpu-box:8000/v1"
    assert model.temperature == 0.1


def test_legacy_connection_params_rejected_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "lubanagent.model.openai_compat.ChatOpenAI", lambda **kwargs: calls.append(kwargs)
    )
    for params in (
        {"api_key": "sk-synthetic-secret"},
        {"model": "wrong-model"},
        {"base_url": "http://evil.invalid/v1"},
        {"default_headers": {"Authorization": "Bearer sk-synthetic-secret"}},
    ):
        definition = ModelDefinition.model_construct(
            key="legacy",
            provider="ollama",
            model_name="qwen3",
            factory="openai_compat",
            params=params,
        )
        with pytest.raises(LubanError) as exc_info:
            create_openai_compatible(definition)
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
        assert "sk-synthetic-secret" not in str(exc_info.value)
    assert calls == []


def test_whitelist_and_redacted_placeholder_rejected_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "lubanagent.model.openai_compat.ChatOpenAI", lambda **kwargs: calls.append(kwargs)
    )
    for params in (
        {"temperature": 0.1, "custom_flag": 1},
        {"temperature": "[REDACTED]"},
    ):
        definition = ModelDefinition.model_construct(
            key="legacy",
            provider="ollama",
            model_name="qwen3",
            factory="openai_compat",
            params=params,
        )
        with pytest.raises(LubanError) as exc_info:
            create_openai_compatible(definition)
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
        assert str(params) not in str(exc_info.value)
    assert calls == []


def test_unknown_provider_raises_validation_error() -> None:
    with pytest.raises(LubanError) as exc_info:
        openai_compat_definition(key="x", provider="unknown", model_name="m")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
