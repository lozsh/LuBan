"""装饰器行为: @tool 描述符生成、@graph/@hook 注册、装饰后原对象可用。"""

from typing import Any

from lubanagent.core import RiskLevel, RunContext, Step, ToolDescriptor
from lubanagent.extension import Registry, graph, hook, tool


def test_tool_descriptor_generation() -> None:
    reg = Registry()

    @tool(
        key="biz.refund",
        description="退款",
        risk=RiskLevel.HIGH,
        timeout_seconds=10.0,
        max_retries=2,
        idempotent=True,
        approval_required=True,
        registry=reg,
    )
    def refund(order_id: str, amount: float, reason: str = "") -> str:
        return f"{order_id}:{amount}:{reason}"

    registered = reg.get_tool("biz.refund")
    descriptor = registered.descriptor
    assert descriptor.name == "refund"
    assert descriptor.description == "退款"
    assert descriptor.risk == RiskLevel.HIGH
    assert descriptor.contract.timeout_seconds == 10.0
    assert descriptor.contract.max_retries == 2
    assert descriptor.contract.idempotent is True
    assert descriptor.contract.approval_required is True
    assert len(descriptor.signature_hash) == 16

    schema = descriptor.args_schema
    assert set(schema["properties"]) == {"order_id", "amount", "reason"}
    assert schema["properties"]["order_id"]["type"] == "string"
    assert schema["properties"]["amount"]["type"] == "number"
    assert schema["properties"]["reason"]["default"] == ""
    assert set(schema["required"]) == {"order_id", "amount"}


def test_tool_default_key_and_callable() -> None:
    reg = Registry()

    @tool(registry=reg)
    def ping() -> str:
        return "pong"

    assert reg.has_tool("ping")
    assert ping() == "pong"


def test_graph_decorator_registers_and_returns_fn() -> None:
    reg = Registry()

    @graph(description="流程", registry=reg)
    def build() -> str:
        return "compiled"

    descriptor = reg.get_graph("build")
    assert descriptor.description == "流程"
    assert descriptor.builder is build
    assert build() == "compiled"


def test_hook_decorator_registers_instance_and_returns_class() -> None:
    reg = Registry()

    @hook(registry=reg)
    class MyHook:
        async def pre_run(self, ctx: RunContext) -> None:
            return None

        async def pre_tool(
            self, ctx: RunContext, tool: ToolDescriptor, args: dict[str, Any]
        ) -> None:
            return None

        async def post_step(self, ctx: RunContext, step: Step) -> None:
            return None

    hooks = reg.hooks()
    assert len(hooks) == 1
    assert isinstance(hooks[0], MyHook)
