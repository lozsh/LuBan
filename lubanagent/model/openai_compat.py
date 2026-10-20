"""OpenAI 兼容协议接入: DeepSeek/Qwen/GLM/vLLM/Ollama 一个工厂全覆盖。"""

import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from lubanagent.core import ErrorCode, LubanError, ModelCapability, ModelDefinition
from lubanagent.core.model import validate_model_connection

PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"},
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
    "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPUAI_API_KEY"},
    "vllm": {},  # base_url 由 ModelDefinition 提供
    "ollama": {"base_url": "http://localhost:11434/v1"},  # 本地无密钥
}


def create_openai_compatible(definition: ModelDefinition) -> BaseChatModel:
    """按 ModelDefinition 构造 ChatOpenAI; 密钥只来自 definition.api_key_env 指向的环境变量。"""
    base_url = definition.base_url or PROVIDERS.get(definition.provider, {}).get("base_url")
    try:
        validate_model_connection(
            factory="openai_compat",
            params=definition.params,
            base_url=base_url,
            api_key_env=definition.api_key_env,
        )
    except ValueError as exc:
        raise LubanError(ErrorCode.VALIDATION_ERROR, str(exc)) from None
    api_key_env = definition.api_key_env
    if api_key_env is None:
        api_key = "EMPTY"  # ollama 等本地服务无密钥, OpenAI 客户端要求非空
    else:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"环境变量 {api_key_env} 未设置, 无法构造模型 {definition.key}",
                details={"api_key_env": api_key_env},
            )
    kwargs: dict[str, Any] = {"model": definition.model_name, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    # params 同时承载「模型构造参数」(temperature/max_tokens 等) 与「框架计费字段」。
    # 计费字段不是 ChatOpenAI 的合法入参，透传会被 SDK 拒收 (TypeError)，构造前摘出。
    ctor_params = {
        k: v
        for k, v in definition.params.items()
        if k not in _FRAMEWORK_RESERVED_PARAMS
    }
    kwargs.update(ctor_params)
    return ChatOpenAI(**kwargs)


# params 里由框架自身消费、不应透传给底层模型客户端的保留字段。
# 计费: input_price_per_1k / output_price_per_1k 由 BudgetGuard 读取 (见 agent_loop)。
_FRAMEWORK_RESERVED_PARAMS: frozenset[str] = frozenset(
    {"input_price_per_1k", "output_price_per_1k"}
)


def openai_compat_definition(
    *,
    key: str,
    provider: str,
    model_name: str,
    capability: ModelCapability | None = None,
    params: dict[str, Any] | None = None,
    base_url: str | None = None,
) -> ModelDefinition:
    """便捷构造: base_url/api_key_env 从 PROVIDERS 预填, 可被显式参数覆盖。"""
    preset = PROVIDERS.get(provider)
    if preset is None:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"未知 provider: {provider}",
            details={"provider": provider},
        )
    return ModelDefinition(
        key=key,
        provider=provider,
        model_name=model_name,
        factory="openai_compat",
        capability=capability if capability is not None else ModelCapability(),
        params=params if params is not None else {},
        base_url=base_url or preset.get("base_url"),
        api_key_env=preset.get("api_key_env"),
    )
