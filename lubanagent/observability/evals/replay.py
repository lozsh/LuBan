"""录制重放：从已有 Run 的 Trace 提取模型脚本与工具录制结果，无副作用安全重跑（H10）。

- script 来源 = MODEL_CALL_COMPLETED 事件 payload（H10 schema：text/tool_calls）；
- 工具结果来源 = TOOL_CALL_COMPLETED / TOOL_CALL_FAILED 事件，按全局顺序经 stub
  注册表回喂（不调真实业务 handler）；
- 模型经 ``RunRequest.model_override`` 做 Run 局部注入——不查不改共享 brain mapping、
  不写 models 表（``REPLAY_MODEL_KEY`` 仅作为新 Run 的 model_key 落库可审计）；
- 重放 Run 的 ``session_id=None`` 隔离会话（会话/摘要记忆零读写）；
- 录制内容含 ``[REDACTED]`` 时显式拒绝（H07 边界：脱敏记录不能当真实数据重放）。
"""

import json
from typing import Any, Literal

from lubanagent.config import Settings
from lubanagent.core import (
    AgentDefinition,
    ErrorCode,
    LubanError,
    RiskLevel,
    Run,
    ToolContract,
    ToolDescriptor,
    TraceEvent,
    TraceEventType,
)
from lubanagent.extension import Registry
from lubanagent.model import fake_definition
from lubanagent.persistence import AgentRepository, Database, RunRepository, TraceRepository

REPLAY_MODEL_KEY = "replay-fake"

RedactionMode = Literal["safe", "live"]

_REDACTED_MARK = "[REDACTED]"


def _contains_redacted(value: Any) -> bool:
    return _REDACTED_MARK in json.dumps(value, ensure_ascii=False, default=str)


def script_from_events(events: list[TraceEvent]) -> list[Any]:
    """MODEL_CALL_COMPLETED 按序提取 text/tool_calls → ScriptedFakeChatModel script 条目。

    条目形状与 fakes.py 的 script 约定一致：有工具调用 → {"tool_calls": [...]}，
    否则 → 文本 str（同时含文本与工具调用的消息，工具调用优先、文本舍弃）。
    录制条目含 [REDACTED]（args 被脱敏）→ VALIDATION_ERROR 显式拒绝。
    """
    script: list[Any] = []
    for event in events:
        if event.type is not TraceEventType.MODEL_CALL_COMPLETED:
            continue
        tool_calls = event.payload.get("tool_calls") or []
        if tool_calls:
            entry = {
                "tool_calls": [
                    {
                        "name": call.get("name"),
                        "args": dict(call.get("args") or {}),
                        "id": call.get("id"),
                    }
                    for call in tool_calls
                ]
            }
            if _contains_redacted(entry):
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    "重放失败：录制事件 tool_calls.args 含 [REDACTED] 脱敏占位，"
                    "不能当真实参数重放（H10 安全重放显式拒绝）",
                    details={"event_id": event.id},
                )
            script.append(entry)
        else:
            script.append(str(event.payload.get("text") or ""))
    return script


def tool_results_from_events(events: list[TraceEvent]) -> list[dict[str, Any]]:
    """按全局顺序提取录制工具结果（TOOL_CALL_COMPLETED/FAILED），供 stub 注册表回喂。

    含 [REDACTED]（result/error 被脱敏）→ VALIDATION_ERROR 显式拒绝。
    """
    results: list[dict[str, Any]] = []
    for event in events:
        if event.type is TraceEventType.TOOL_CALL_COMPLETED:
            record: dict[str, Any] = {"ok": True, "result": event.payload.get("result")}
        elif event.type is TraceEventType.TOOL_CALL_FAILED:
            record = {"ok": False, "error": event.payload.get("error")}
        else:
            continue
        if _contains_redacted(record):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                "重放失败：录制工具结果含 [REDACTED] 脱敏占位，"
                "不能当真实结果回喂（H10 安全重放显式拒绝）",
                details={"event_id": event.id},
            )
        results.append(record)
    return results


def _replay_tool_registry(
    definition: AgentDefinition, base: Registry, results: list[dict[str, Any]]
) -> Registry:
    """stub 注册表：按定义的工具键复制描述符（降为 LOW 风险），handler 回喂录制结果。

    handler 只消费录制序列，不执行真实业务逻辑；调用次数超出录制数 → VALIDATION_ERROR
    （历史不足时明确失败，不宣称完整确定性复现）。
    """
    replay_registry = Registry()
    state = {"cursor": 0}

    async def stub_handler(**kwargs: Any) -> str:
        cursor = state["cursor"]
        if cursor >= len(results):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                "重放失败：工具录制结果不足（真实调用次数将超出录制），历史不完整",
                details={"called": cursor + 1, "recorded": len(results)},
            )
        record = results[cursor]
        state["cursor"] = cursor + 1
        if record["ok"]:
            value = record["result"]
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)
        return json.dumps({"error": record["error"]}, ensure_ascii=False)

    for key in definition.tools:
        if not base.has_tool(key):
            raise LubanError(
                ErrorCode.TOOL_NOT_FOUND,
                f"重放失败：工具未注册，无法构造 stub: {key}",
                details={"tool": key},
            )
        descriptor = base.get_tool(key).descriptor
        replay_registry.register_tool(
            ToolDescriptor(
                key=descriptor.key,
                name=descriptor.name,
                description=descriptor.description,
                args_schema=descriptor.args_schema,
                risk=RiskLevel.LOW,
                contract=ToolContract(),
                signature_hash="",
            ),
            stub_handler,
        )
    return replay_registry


def _safe_replay_definition(definition: AgentDefinition) -> AgentDefinition:
    """重放安全副本：清空 skills/mcp_servers/长期记忆/签名冻结（各自理由见模块 docstring）。"""
    return definition.model_copy(
        update={
            "skills": [],
            "mcp_servers": [],
            "tool_signatures": {},
            "memory": definition.memory.model_copy(update={"long_term": False}),
        }
    )


async def replay_run(
    run_id: str,
    *,
    db: Database,
    registry: Registry,
    settings: Settings,
    mode: RedactionMode = "safe",
    approval_manager: Any | None = None,
) -> Run:
    """读原 Run 的输入与 Trace → safe 模式无副作用重跑 / live 模式显式真实再执行。

    safe（默认）：Run 局部注入 scripted fake 模型与录制结果 stub 工具，零真实 handler、
    零共享状态修改、隔离会话；录制记录缺失/脱敏时显式失败。
    live（显式 opt-in）：真实 brain 解析 + 真实工具（真实副作用）；高风险/审批工具
    无 approval_handler 时由 ToolExecutor 显式拒绝（重新审批闸门保留）。
    """
    if mode not in ("safe", "live"):
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"未知重放模式: {mode}（仅支持 safe / live）",
            details={"mode": mode},
        )
    from lubanagent.runtime import AgentRuntime, RunRequest  # 懒导入避免循环依赖

    async with db.session() as session:
        original = await RunRepository(session).get_run(run_id)
        if original is None:
            raise LubanError(
                ErrorCode.RUN_NOT_FOUND,
                f"重放失败，Run 不存在: {run_id}",
                details={"run_id": run_id},
            )
        events = await TraceRepository(session).list_events(run_id)
        snapshot = await AgentRepository(session).get_version(
            original.agent_key, original.agent_version
        )
    if snapshot is None:
        raise LubanError(
            ErrorCode.AGENT_VERSION_NOT_FOUND,
            f"重放失败，Agent 版本不存在: {original.agent_key} v{original.agent_version}",
            details={"agent_key": original.agent_key, "version": original.agent_version},
        )

    request_kwargs: dict[str, Any] = {
        "agent_key": original.agent_key,
        "user_input": str(original.input.get("user_input", "")),
        # 隔离会话：session_id=None → 会话/摘要记忆零读写（不污染原 session）
        "session_id": None,
        "user_id": original.user_id,
        "version": original.agent_version,
        "variables": original.input.get("variables") or None,
    }

    if mode == "live":
        runtime = AgentRuntime(
            db=db, settings=settings, registry=registry, approval_manager=approval_manager
        )
        return await runtime.run(RunRequest(**request_kwargs))

    script = script_from_events(events)
    if not script:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            "重放失败：原 Run 无 MODEL_CALL_COMPLETED 记录，历史不足，"
            "不宣称完整确定性复现（如需真实再执行请显式 mode='live'）",
            details={"run_id": run_id},
        )
    results = tool_results_from_events(events)
    # 录制完整性前置校验：模型要调的工具次数超出录制结果数 → 显式失败
    #（stub 运行期兜底也有同语义检查；此处保证在产生任何 Run 之前拒绝）
    expected_tool_calls = sum(
        len(entry.get("tool_calls") or []) for entry in script if isinstance(entry, dict)
    )
    if expected_tool_calls > len(results):
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            "重放失败：工具录制结果不足（模型工具调用次数超出录制），历史不完整，"
            "不宣称完整确定性复现",
            details={"expected": expected_tool_calls, "recorded": len(results)},
        )
    definition = snapshot.definition
    definition.version = snapshot.version
    definition.status = snapshot.status
    replay_definition = _safe_replay_definition(definition)
    replay_registry = _replay_tool_registry(replay_definition, registry, results)
    runtime = AgentRuntime(db=db, settings=settings, registry=replay_registry)
    return await runtime.run(
        RunRequest(
            **request_kwargs,
            definition_override=replay_definition,
            model_override=fake_definition(key=REPLAY_MODEL_KEY, script=script),
        )
    )
