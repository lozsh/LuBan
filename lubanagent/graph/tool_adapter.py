"""flow_as_tool：CompiledFlow → RegisteredTool（三种结合形态之三：流程 = 工具）。"""

import hashlib
import json
from typing import Any

from lubanagent.core import RiskLevel, RunContext, ToolContract, ToolDescriptor
from lubanagent.extension import RegisteredTool

from .builder import CompiledFlow
from .runner import GraphRunner


def flow_as_tool(
    flow: CompiledFlow,
    runner: GraphRunner,
    *,
    key: str | None = None,
    description: str | None = None,
    risk: RiskLevel = RiskLevel.MEDIUM,
) -> RegisteredTool:
    """流程包装为工具：args 仅 `input: str`；handler 调 runner.run。

    completed → 返回 output；interrupted → 返回 {"status": "interrupted", ...}
    （由上层/模型决定是否推进，恢复经 API/业务侧 resume）。
    handler 声明 `_ctx`：ToolExecutor 约定注入父 Run 的 RunContext（预算继承/取消传播，
    见 GraphRunner._resolve_guard）；无 _ctx（如离线直调 executor）时按现状不传 parent_ctx。
    """
    tool_key = key or flow.name
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"input": {"type": "string", "description": "流程输入"}},
        "additionalProperties": True,
    }
    signature_hash = hashlib.sha256(
        (tool_key + json.dumps(args_schema, sort_keys=True)).encode()
    ).hexdigest()[:16]
    descriptor = ToolDescriptor(
        key=tool_key,
        name=tool_key,
        description=description or flow.description or f"图编排流程 {flow.name}",
        args_schema=args_schema,
        risk=risk,
        contract=ToolContract(),
        signature_hash=signature_hash,
    )

    async def handler(input: str | None = None, _ctx: RunContext | None = None) -> Any:
        payload = {} if input is None else {"input": input}
        result = await runner.run(flow, payload, parent_ctx=_ctx)
        if result.status == "interrupted":
            return {
                "status": "interrupted",
                "interrupt": result.interrupt,
                "thread_id": result.thread_id,
            }
        return result.output

    return RegisteredTool(descriptor=descriptor, handler=handler)


__all__ = ["flow_as_tool"]
