"""注册装饰器与包发现: 装饰即注册, 返回原对象保持可直接调用。"""

import hashlib
import importlib
import inspect
import logging
import pkgutil
import sys
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import create_model

from lubanagent.core import RiskLevel, ToolContract, ToolDescriptor

from .protocols import GraphDescriptor, Hook
from .registry import Registry, default_registry

logger = logging.getLogger(__name__)


def _args_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """从函数签名 + type hints 生成 JSON Schema。"""
    sig = inspect.signature(fn)
    fields: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = param.annotation if param.annotation is not inspect.Parameter.empty else Any
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (annotation, default)
    model = create_model(f"{fn.__name__}Args", **fields)  # type: ignore[call-overload]
    return model.model_json_schema()


def _signature_hash(fn: Callable[..., Any]) -> str:
    return hashlib.sha256(str(inspect.signature(fn)).encode()).hexdigest()[:16]


def tool(
    *,
    key: str | None = None,
    description: str = "",
    risk: RiskLevel = RiskLevel.LOW,
    timeout_seconds: float = 30.0,
    max_retries: int = 0,
    idempotent: bool = False,
    approval_required: bool = False,
    registry: Registry | None = None,
) -> Callable:
    """装饰函数生成 ToolDescriptor 并注册, 返回原函数。"""
    reg = registry if registry is not None else default_registry

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        descriptor = ToolDescriptor(
            key=key or fn.__name__,
            name=fn.__name__,
            description=description,
            args_schema=_args_schema(fn),
            risk=risk,
            contract=ToolContract(
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                idempotent=idempotent,
                approval_required=approval_required,
            ),
            signature_hash=_signature_hash(fn),
        )
        reg.register_tool(descriptor, fn)
        return fn

    return decorator


def graph(
    *,
    name: str | None = None,
    description: str = "",
    registry: Registry | None = None,
) -> Callable:
    """装饰图构建函数生成 GraphDescriptor 并注册, 返回原函数。"""
    reg = registry if registry is not None else default_registry

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        descriptor = GraphDescriptor(name=name or fn.__name__, description=description, builder=fn)
        reg.register_graph(descriptor)
        return fn

    return decorator


def hook(*, registry: Registry | None = None) -> Callable:
    """装饰 Hook 实现类, 实例化并注册, 返回原类。"""
    reg = registry if registry is not None else default_registry

    def decorator(cls: type[Hook]) -> type[Hook]:
        reg.register_hook(cls())
        return cls

    return decorator


def _registry_size(reg: Registry) -> int:
    return (
        len(reg.list_tools())
        + len(reg.list_graphs())
        + len(reg.hooks())
        + len(reg.list_knowledge())
        + (1 if reg.get_auth() is not None else 0)
        + (1 if reg.get_observability() is not None else 0)
    )


def discover(packages: Iterable[str], *, registry: Registry | None = None) -> int:
    """动态导入每个包及其全部子模块触发装饰器注册, 返回本次新注册条目总数。

    已导入模块跳过再导入(importlib 模块缓存), 重复调用幂等; 包名由调用方传入。
    """
    reg = registry if registry is not None else default_registry
    before = _registry_size(reg)
    for package in packages:
        module = importlib.import_module(package)
        paths = getattr(module, "__path__", None)
        if paths is None:
            continue
        for info in pkgutil.walk_packages(paths, module.__name__ + "."):
            if info.name in sys.modules:
                continue
            importlib.import_module(info.name)
    return _registry_size(reg) - before


def discover_packages(
    packages: Iterable[str], *, registry: Registry | None = None
) -> int:
    """按配置扫描扩展包（discover 的容错版）: 顶层包不存在记 warning 跳过, 其余原样抛。

    供 create_app 装配与 `luban eval run` CLI 共用——两处都必须在构建 Runtime 前完成
    扩展注册, 否则带工具的 Agent 在独立 CLI 进程中因注册表为空而全量不可运行
    （表现为「引用的工具已注销」）。包内部 import 失败不吞（真实配置错误要暴露）。
    """
    registered = 0
    for package in packages:
        try:
            registered += discover([package], registry=registry)
        except ModuleNotFoundError as exc:
            if exc.name != package:  # 包内部 import 失败不吞
                raise
            logger.warning("扩展包不存在，跳过扫描: %s", package)
    return registered
