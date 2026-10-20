"""模型行为: frozen 版本快照、token 合计、运行上下文。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lubanagent.core import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    BrainProfile,
    Budget,
    ModelDefinition,
    RunContext,
    TokenEstimate,
    TokenUsage,
)

NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def test_model_definition_rejects_secret_and_connection_params() -> None:
    base = {"key": "m", "provider": "ollama", "model_name": "m", "factory": "openai_compat"}
    for params in (
        {"api_key": "sk-synthetic-secret"},
        {"model": "other"},
        {"default_headers": {"Authorization": "Bearer secret"}},
        {"nested": {"x-api-key": "secret"}},
        {"endpoint": "https://user:secret@host.invalid/v1"},
    ):
        with pytest.raises(ValidationError) as exc_info:
            ModelDefinition(**base, params=params)
        assert "sk-synthetic-secret" not in str(exc_info.value)
    for base_url in (
        "https://user:secret@host.invalid/v1",
        "https://host.invalid/v1?api_key=secret",
    ):
        with pytest.raises(ValidationError) as exc_info:
            ModelDefinition(**base, base_url=base_url)
        assert "secret@" not in str(exc_info.value)
    valid = ModelDefinition(
        **base,
        base_url="http://gpu-box:8000/v1",
        params={"temperature": 0.1, "max_tokens": 128, "input_price_per_1k": 0.001},
    )
    assert valid.params["max_tokens"] == 128


def test_model_definition_api_key_env_must_be_variable_name() -> None:
    with pytest.raises(ValidationError):
        ModelDefinition(
            key="m",
            provider="ollama",
            model_name="m",
            factory="openai_compat",
            api_key_env="sk-synthetic-secret",
        )


def test_openai_compat_params_whitelist_rejects_unknown_keys() -> None:
    base = {"key": "m", "provider": "ollama", "model_name": "m", "factory": "openai_compat"}
    for params in (
        {"custom_flag": 1},
        {"temperature": 0.1, "unknown_tool_choice": "auto"},
    ):
        with pytest.raises(ValidationError):
            ModelDefinition(**base, params=params)


def test_redact_model_params_builds_display_copy_only() -> None:
    from lubanagent.core.model import redact_model_params

    source = {
        "temperature": 0.3,
        "api_key": "sk-synthetic-secret",
        "base_url": "http://host.internal/v1",
        "notes": ["Bearer sk-list-secret", {"nested_token": "sk-nested-secret"}],
        "callback": "https://local.invalid?api_key=sk-url-secret",
        "plain": "ok",
    }
    redacted = redact_model_params(source)
    assert redacted["temperature"] == 0.3
    assert redacted["plain"] == "ok"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["base_url"] == "[REDACTED]"
    assert "sk-list-secret" not in str(redacted["notes"])
    assert "sk-nested-secret" not in str(redacted["notes"])
    assert redacted["callback"] == "[REDACTED]"
    # 展示副本不改源数据
    assert source["api_key"] == "sk-synthetic-secret"


def test_validate_model_connection_rejects_bad_env_and_base_url() -> None:
    from lubanagent.core.model import validate_model_connection

    with pytest.raises(ValueError):
        validate_model_connection(
            factory="openai_compat", params={}, base_url=None, api_key_env="sk-synthetic-secret"
        )
    with pytest.raises(ValueError):
        validate_model_connection(
            factory="openai_compat",
            params={},
            base_url="https://user:secret@host.invalid/v1",
            api_key_env=None,
        )


def test_agent_version_frozen() -> None:
    version = AgentVersion(
        agent_key="cs-agent",
        version=1,
        definition=AgentDefinition(key="cs-agent", name="客服"),
        status=AgentStatus.PUBLISHED,
        published_at=NOW,
    )
    with pytest.raises(ValidationError):
        version.status = AgentStatus.DEPRECATED  # type: ignore[misc]
    with pytest.raises(ValidationError):
        version.version = 2  # type: ignore[misc]


def test_agent_version_round_trip() -> None:
    version = AgentVersion(
        agent_key="cs-agent",
        version=2,
        definition=AgentDefinition(key="cs-agent", name="客服", version=2),
        status=AgentStatus.PUBLISHED,
    )
    payload = version.model_dump(mode="json")
    restored = AgentVersion.model_validate(payload)
    assert restored.published_at is None
    assert restored.definition.key == "cs-agent"


def test_token_usage_total() -> None:
    usage = TokenUsage(input_tokens=100, output_tokens=50, model_calls=3)
    assert usage.total == 150


def test_token_estimate_total() -> None:
    estimate = TokenEstimate(input_tokens=200, output_tokens=100)
    assert estimate.total == 300


def test_budget_defaults_unlimited() -> None:
    budget = Budget()
    assert budget.max_tokens is None
    assert budget.max_cost is None
    assert budget.max_seconds is None


def test_run_context_generic_deps() -> None:
    ctx: RunContext[dict[str, str]] = RunContext(
        run_id="r1",
        agent_key="a",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        deps={"db": "conn"},
        metadata={"channel": "test"},
    )
    assert ctx.deps == {"db": "conn"}
    assert ctx.usage.total == 0
    assert ctx.budget is None
    assert ctx.model_key is None
