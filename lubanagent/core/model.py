"""模型定义: 模型注册键、能力清单与固定使用参数, 不存密钥本体。"""

import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lubanagent.core.redact import _REDACTED, _unsafe_url, is_secret_key, normalized_key

_CONNECTION_KEYS = frozenset(
    {
        "model",
        "modelname",
        "apikey",
        "apikeyenv",
        "baseurl",
        "apibase",
        "openaiapibase",
        "openaiapikey",
        "openaiorganization",
        "organization",
        "defaultheaders",
        "defaultquery",
        "httpclient",
        "httpasyncclient",
        "proxy",
        "proxies",
        "transport",
        "rootclient",
        "rootasyncclient",
        "clientoptions",
        "endpoint",
    }
)
_OPENAI_PARAMS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "n",
        "stop",
        "seed",
        "timeout",
        "max_retries",
        "streaming",
        "input_price_per_1k",
        "output_price_per_1k",
    }
)


def redact_registered_model_params(factory: str, params: dict[str, Any]) -> dict[str, Any]:
    """管理界面只展示已知 OpenAI 推理/计价字段；旧自定义字段不展示值。"""
    if factory != "openai_compat":
        return {key: _REDACTED for key in params}
    return {
        key: redact_model_params(value)
        if key in _OPENAI_PARAMS and not is_secret_key(key)
        else _REDACTED
        for key, value in params.items()
    }


def _unsafe_connection_url(value: str) -> bool:
    parsed = urlsplit(value)
    return _unsafe_url(value) or bool(parsed.query or parsed.fragment)


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            is_secret_key(str(key)) or _contains_secret(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and _unsafe_url(value)


def validate_model_connection(
    *, factory: str, params: dict[str, Any], base_url: str | None, api_key_env: str | None
) -> None:
    """模型入库/构造边界；错误消息不包含输入值或密钥。"""
    if api_key_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
        raise ValueError("api_key_env 必须是环境变量名")
    if base_url and _unsafe_connection_url(base_url):
        raise ValueError("连接地址不得包含凭证、查询参数或片段")
    if any(normalized_key(str(key)) in _CONNECTION_KEYS for key in params):
        raise ValueError("params 不得覆盖模型或连接字段")
    if _contains_secret(params) or _REDACTED in str(params):
        raise ValueError("params 不得包含连接凭证、授权头或展示占位值")
    if factory == "openai_compat" and any(key not in _OPENAI_PARAMS for key in params):
        raise ValueError("OpenAI 兼容模型 params 仅允许受控推理与计价参数")


def redact_model_params(value: Any) -> Any:
    """只构造展示副本；可疑 URL 整体隐藏，不能把展示副本用于执行。"""
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if is_secret_key(str(key))
                or normalized_key(str(key)) in _CONNECTION_KEYS
                else redact_model_params(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_model_params(item) for item in value]
    if isinstance(value, str) and (
        _unsafe_url(value) or "bearer " in value.lower() or value == _REDACTED
    ):
        return _REDACTED
    return value


class ModelCapability(BaseModel):
    """模型能力清单, 大脑映射与运行前能力校验的依据。"""

    chat: bool = True
    streaming: bool = True
    tool_calling: bool = False
    structured_output: bool = False
    vision: bool = False
    reasoning: bool = False
    context_window: int = 8192
    max_output: int = 4096


class ModelDefinition(BaseModel):
    """模型注册记录; 切换模型 = 映射指向另一个 key, 不发版。"""

    model_config = ConfigDict(hide_input_in_errors=True)

    key: str
    provider: str
    model_name: str
    factory: str
    capability: ModelCapability = Field(default_factory=ModelCapability)
    params: dict[str, Any] = Field(default_factory=dict)
    base_url: str | None = None
    api_key_env: str | None = None
    enabled: bool = True

    @field_validator("api_key_env")
    @classmethod
    def _validate_env_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("api_key_env 必须是环境变量名")
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value and _unsafe_connection_url(value):
            raise ValueError("连接地址不得包含凭证、查询参数或片段")
        return value

    @model_validator(mode="after")
    def _validate_params(self) -> "ModelDefinition":
        validate_model_connection(
            factory=self.factory,
            params=self.params,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
        )
        return self
