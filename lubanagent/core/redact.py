"""共享脱敏组件：字段级秘密键规则 + 展示副本构造，供 Trace/SSE/观测外发边界复用。

策略边界：
- 只做字段级键名规则（秘密键集合 + 后缀）与凭证形态字符串识别，
  不对任意业务文本做 PII 值级检测——那不可能靠规则保证。
- `redact_value` 只构造展示副本，绝不修改源数据；执行路径必须用真实参数。
- 异常 traceback 仅进日志，是否落盘由部署方日志策略管控，本模块不处理。
- 部署者可用 `register_secret_keys` 注册业务特有的敏感键名（大小写/分隔符不敏感）。
"""

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

_REDACTED = "[REDACTED]"

_SECRET_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "proxyauthorization",
        "token",
        "accesstoken",
        "refreshtoken",
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "privatekey",
        "xapikey",
        "credential",
        "credentials",
        "secretkey",
        "bearertoken",
        "defaultheaders",
        "headers",
        "httpclient",
        "httpasyncclient",
        "clientoptions",
    }
)

_SECRET_KEY_SUFFIXES = (
    "apikey",
    "accesskey",
    "authkey",
    "token",
    "password",
    "secret",
    "credential",
)

_extra_secret_keys: set[str] = set()


def register_secret_keys(*keys: str) -> None:
    """注册额外敏感键名（部署者自定义脱敏；归一化后与内置规则同权命中）。"""
    for key in keys:
        normalized = normalized_key(key)
        if normalized:
            _extra_secret_keys.add(normalized)


def normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def is_secret_key(key: str) -> bool:
    normalized = normalized_key(key)
    return (
        normalized in _SECRET_KEYS
        or normalized in _extra_secret_keys
        or normalized.endswith(_SECRET_KEY_SUFFIXES)
    )


def _unsafe_url(value: str) -> bool:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return False
    return bool(
        parsed.username is not None
        or parsed.password is not None
        or any(is_secret_key(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
    )


def redact_value(value: Any) -> Any:
    """构造脱敏展示副本：秘密键/连接形态凭证 URL/bearer 字符串整体隐藏。

    只读源数据并返回新结构；调用方拿副本做审计展示，执行仍用原值。
    """
    if isinstance(value, dict):
        return {
            key: _REDACTED if is_secret_key(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        redacted = [redact_value(item) for item in value]
        return type(value)(redacted) if isinstance(value, tuple) else redacted
    if isinstance(value, str) and (_unsafe_url(value) or "bearer " in value.lower()):
        return _REDACTED
    return value
