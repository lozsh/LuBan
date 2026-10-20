"""AgentRuntime 验收测试：SSE 序列/三道硬边界/取消/能力校验/审批/版本校验/Trace 七问/观测双写。"""

import asyncio
import json
from collections import Counter
from datetime import datetime

import pytest
from sqlalchemy import select

from lubanagent.core import (
    BoundaryConfig,
    BrainConfig,
    BrainProfile,
    Budget,
    EndReason,
    ErrorCode,
    LubanError,
    ModelCapability,
    RunStatus,
    TraceEvent,
    TraceEventType,
)
from lubanagent.extension import tool
from lubanagent.model import fake_definition
from lubanagent.persistence import ModelRepository, RunRepository, TraceRepository
from lubanagent.persistence.models import TokenUsageRow
from lubanagent.runtime import RunRequest, sse_stream
from lubanagent.tool import ApprovalManager

from .conftest import (
    RuntimeFakeChatModel,
    UsageFakeChatModel,
    create_test_agent,
    register_echo,
    seed_fake_brain,
)

ECHO_CALL = {"tool_calls": [{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]}


# ------------------------------------------------------------ H09：默认装配能力兑现


async def test_summary_memory_default_summarizer_from_cheap_slot(
    db, registry, make_runtime
):
    """未注入 summarizer → 按 cheap 槽位 lazy 构造 LlmSummarizer，压缩真实发生。"""
    from lubanagent.core import AgentDefinition, MemoryConfig
    from lubanagent.persistence.models.memory import SessionSummaryRow

    await seed_fake_brain(db, script=["主答案"], profile=BrainProfile.SMART)
    await seed_fake_brain(
        db, script=["cheap 摘要结果"], profile=BrainProfile.CHEAP, key="fake-cheap"
    )
    definition = AgentDefinition(
        key="test-agent",
        name="test-agent",
        instructions="你是测试助手。",
        memory=MemoryConfig(session=True, summary=True, window_size=1),
    )
    from lubanagent.persistence import AgentRepository

    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    runtime = make_runtime()  # 不注入 summarizer
    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="记住这句话", session_id="s-sum")
    )
    assert run.status is RunStatus.COMPLETED
    async with db.session() as session:
        rows = (
            (await session.execute(select(SessionSummaryRow))).scalars().all()
        )
    assert any(row.summary == "cheap 摘要结果" for row in rows)


async def test_mcp_tools_discovered_for_definition_servers(db, registry, make_runtime):
    """definition.mcp_servers 非空 → run 期发现远端工具注册进 registry（幂等，可多轮 run）。"""
    from lubanagent.core import AgentDefinition
    from lubanagent.mcp import McpClient, McpRegistry, McpServerConfig
    from lubanagent.persistence import AgentRepository
    from tests.lubanagent.mcp.conftest import make_remote_server, memory_session_factory

    mcp_reg = McpRegistry(
        client_factory=lambda cfg: McpClient(
            cfg, session_factory=memory_session_factory(make_remote_server())
        )
    )
    mcp_reg.register(McpServerConfig(key="srv", name="Remote", command="python"))
    descriptors = await mcp_reg.discover_tools("srv", into=registry)
    add_tool = next(d for d in descriptors if d.key == "mcp:srv:add")

    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": add_tool.key, "args": {"a": 2, "b": 1}, "id": "c1"}]},
            "计算完成",
        ],
    )
    definition = AgentDefinition(
        key="test-agent",
        name="test-agent",
        instructions="你是测试助手。",
        tools=[add_tool.key],
        tool_signatures={add_tool.key: add_tool.signature_hash},
        mcp_servers=["srv"],
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    runtime = make_runtime(mcp_registry=mcp_reg)
    for i in range(2):  # 第二轮验证重复发现不炸（幂等）
        events = await collect(
            runtime, RunRequest(agent_key="test-agent", user_input="算一下", run_id=f"mcp-{i}")
        )
        run = await get_run(db, f"mcp-{i}")
        assert run.status is RunStatus.COMPLETED
        completed = next(e for e in events if e.type is TraceEventType.TOOL_CALL_COMPLETED)
        assert str(completed.payload["result"]) == "3"  # 远端 add(2,1) 真实执行


async def test_mcp_servers_without_registry_fails_run(db, registry, make_runtime):
    """mcp_servers 非空且未注入 McpRegistry → RUN_FAILED CONFIG_ERROR，不静默看似开启。"""
    from lubanagent.core import AgentDefinition
    from lubanagent.persistence import AgentRepository

    await seed_fake_brain(db, script=["占位"])
    definition = AgentDefinition(
        key="test-agent",
        name="test-agent",
        instructions="你是测试助手。",
        mcp_servers=["srv"],
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))
    assert run.status is RunStatus.FAILED
    assert run.error is not None and run.error["code"] == ErrorCode.CONFIG_ERROR.value
    assert "McpRegistry" in run.error["message"]


async def collect(runtime, request) -> list[TraceEvent]:
    return [event async for event in runtime.stream(request)]


def sse_names(frames: list[str]) -> list[str]:
    return [
        line[len("event: ") :]
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("event: ")
    ]


async def get_run(db, run_id: str):
    async with db.session() as session:
        return await RunRepository(session).get_run(run_id)


async def get_events(db, run_id: str) -> list[TraceEvent]:
    async with db.session() as session:
        return await TraceRepository(session).list_events(run_id)


# ------------------------------------------------------------- SSE 事件流


async def test_sse_event_sequence(db, registry, make_runtime, monkeypatch):
    descriptor = register_echo(registry)
    await seed_fake_brain(db, script=["占位"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    # 首条响应同时携带文本与工具调用（模型边说边调），再补最终答案
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: RuntimeFakeChatModel(
            script=[{"content": "先想一下", **ECHO_CALL}, "最终答案"]
        ),
    )
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="你好")

    frames = [frame async for frame in sse_stream(runtime.stream(request))]
    names = sse_names(frames)

    assert names[0] == "run.started"
    assert names[-1] == "run.completed"
    assert "token.delta" in names
    # run.started → token.delta → tool.started → tool.finished → run.completed
    assert names.index("run.started") < names.index("token.delta")
    assert names.index("token.delta") < names.index("tool.started")
    assert names.index("tool.started") < names.index("tool.finished")
    assert names.index("tool.finished") < names.index("run.completed")
    # 终态唯一
    terminals = [n for n in names if n in {"run.completed", "run.failed", "run.cancelled"}]
    assert terminals == ["run.completed"]


async def test_run_returns_terminal_run_from_db(db, registry, make_runtime):
    descriptor = register_echo(registry)
    await seed_fake_brain(db, script=[ECHO_CALL, "最终答案"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))
    assert run.status is RunStatus.COMPLETED
    assert run.end_reason is EndReason.FINAL_ANSWER
    assert run.output == "最终答案"


async def test_parent_run_id_passthrough(db, registry, make_runtime):
    await seed_fake_brain(db, script=["ok"])
    await create_test_agent(db)
    runtime = make_runtime()
    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="子任务", parent_run_id="parent-1")
    )
    assert run.parent_run_id == "parent-1"


# ------------------------------------------------------------- 预算熔断


async def test_budget_max_tokens_exceeded(db, registry, make_runtime, monkeypatch):
    await seed_fake_brain(db, script=["占位"])  # brain 映射就位，实际模型由 monkeypatch 接管
    await create_test_agent(db, boundary=BoundaryConfig(budget=Budget(max_tokens=5)))
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=["超长回答"], usage=(10, 10)),
    )
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="烧钱")

    events = await collect(runtime, request)
    types = [event.type for event in events]
    assert TraceEventType.BUDGET_EXCEEDED in types
    assert types[-1] is TraceEventType.RUN_FAILED
    assert events[-1].payload["end_reason"] == EndReason.BUDGET_EXCEEDED.value

    run = await get_run(db, request.run_id or events[0].run_id)
    assert run.status is RunStatus.FAILED
    assert run.end_reason is EndReason.BUDGET_EXCEEDED
    db_types = [event.type for event in await get_events(db, run.id)]
    assert TraceEventType.BUDGET_EXCEEDED in db_types
    assert db_types[-1] is TraceEventType.RUN_FAILED
    async with db.session() as session:
        usage_rows = (
            await session.scalars(select(TokenUsageRow).where(TokenUsageRow.run_id == run.id))
        ).all()
    assert len(usage_rows) == 1
    assert usage_rows[0].input_tokens == 10


async def test_budget_max_cost_exceeded_with_pricing(db, registry, make_runtime, monkeypatch):
    await seed_fake_brain(db, script=["占位"], pricing=(1.0, 1.0))
    await create_test_agent(db, boundary=BoundaryConfig(budget=Budget(max_cost=0.001)))
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=["贵回答"], usage=(10, 10)),
    )
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="贵"))
    types = [event.type for event in events]
    assert TraceEventType.BUDGET_EXCEEDED in types
    assert types[-1] is TraceEventType.RUN_FAILED


async def test_budget_warning_no_pricing(db, registry, make_runtime):
    await seed_fake_brain(db, script=["正常回答"])  # fake 模型无价格信息
    await create_test_agent(db, boundary=BoundaryConfig(budget=Budget(max_cost=1.0)))
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="问"))
    warnings = [e for e in events if e.type is TraceEventType.BUDGET_WARNING]
    assert len(warnings) == 1
    assert warnings[0].payload["reason"] == "no_pricing"
    assert events[-1].type is TraceEventType.RUN_COMPLETED  # 无价格信息不熔断


# ------------------------------------------------------------- 步数 / 超时 / 取消


async def test_step_limit_via_recursion_limit(db, registry, make_runtime):
    descriptor = register_echo(registry)
    script = [
        {"tool_calls": [{"name": "echo", "args": {"text": str(i)}, "id": f"c{i}"}]}
        for i in range(8)
    ]
    await seed_fake_brain(db, script=script)
    await create_test_agent(
        db,
        tools=["echo"],
        tool_signatures={"echo": descriptor.signature_hash},
        boundary=BoundaryConfig(max_steps=1),
    )
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="循环")
    events = await collect(runtime, request)
    assert events[-1].type is TraceEventType.RUN_FAILED
    assert events[-1].payload["end_reason"] == EndReason.STEP_LIMIT.value
    run = await get_run(db, events[0].run_id)
    assert run.end_reason is EndReason.STEP_LIMIT


async def test_timeout_with_slow_tool(db, registry, make_runtime):
    @tool(registry=registry, description="慢工具", timeout_seconds=30.0)
    async def slow(text: str) -> str:
        await asyncio.sleep(0.5)
        return "slow done"

    descriptor = registry.get_tool("slow").descriptor
    await seed_fake_brain(
        db,
        script=[{"tool_calls": [{"name": "slow", "args": {"text": "x"}, "id": "c1"}]}, "done"],
    )
    await create_test_agent(
        db,
        tools=["slow"],
        tool_signatures={"slow": descriptor.signature_hash},
        boundary=BoundaryConfig(timeout_seconds=0.05),
    )
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="等")
    events = await collect(runtime, request)
    assert events[-1].type is TraceEventType.RUN_FAILED
    assert events[-1].payload["end_reason"] == EndReason.TIMEOUT.value
    run = await get_run(db, events[0].run_id)
    assert run.end_reason is EndReason.TIMEOUT


async def test_cancel_mid_run(db, registry, make_runtime):
    @tool(registry=registry, description="慢工具", timeout_seconds=30.0)
    async def slow(text: str) -> str:
        await asyncio.sleep(0.5)
        return "slow done"

    descriptor = registry.get_tool("slow").descriptor
    await seed_fake_brain(
        db,
        script=[{"tool_calls": [{"name": "slow", "args": {"text": "x"}, "id": "c1"}]}, "done"],
    )
    await create_test_agent(db, tools=["slow"], tool_signatures={"slow": descriptor.signature_hash})
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="停", run_id="cancel-run-1")

    events: list[TraceEvent] = []
    async for event in runtime.stream(request):
        events.append(event)
        if event.type is TraceEventType.TOOL_CALL_STARTED:
            assert await runtime.cancel("cancel-run-1") is True

    assert events[-1].type is TraceEventType.RUN_CANCELLED
    run = await get_run(db, "cancel-run-1")
    assert run.status is RunStatus.CANCELLED
    assert run.end_reason is EndReason.CANCELLED


# ------------------------------------------------------------- 能力校验与降级


async def test_capability_mismatch_without_fallback_fails_fast(db, registry, make_runtime):
    register_echo(registry)
    incapable = ModelCapability(chat=True, tool_calling=False)
    await seed_fake_brain(db, script=["不会用到"], capability=incapable)
    await create_test_agent(db, tools=["echo"])
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="问"))
    assert [event.type for event in events] == [TraceEventType.RUN_FAILED]
    assert events[0].payload["error"]["code"] == ErrorCode.CAPABILITY_MISMATCH.value


async def test_capability_fallback_emits_model_fallback_before_completed(
    db, registry, make_runtime
):
    register_echo(registry)
    incapable = ModelCapability(chat=True, tool_calling=False)
    await seed_fake_brain(db, script=["不会用到"], capability=incapable, key="fake-weak")
    await seed_fake_brain(db, script=["降级回答"], profile=BrainProfile.FAST, key="fake-strong")
    await create_test_agent(
        db,
        tools=["echo"],
        brain=BrainConfig(profile=BrainProfile.SMART, fallback_profiles=[BrainProfile.FAST]),
    )
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="问"))
    types = [event.type for event in events]
    assert types[0] is TraceEventType.MODEL_FALLBACK
    assert types[-1] is TraceEventType.RUN_COMPLETED
    fallback = events[0]
    assert fallback.payload["from_profile"] == "smart"
    assert fallback.payload["to_profile"] == "fast"
    assert fallback.payload["model_key"] == "fake-strong"


# ------------------------------------------------------------- 审批


async def test_approval_gate_flow(db, registry, make_runtime):
    descriptor = register_echo(registry, approval_required=True)
    await seed_fake_brain(db, script=[ECHO_CALL, "审批后完成"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    manager = ApprovalManager()
    runtime = make_runtime(approval_manager=manager)
    request = RunRequest(agent_key="test-agent", user_input="审批", run_id="approval-run-1")

    events: list[TraceEvent] = []
    saw_waiting = False
    async for event in runtime.stream(request):
        events.append(event)
        if event.type is TraceEventType.TOOL_APPROVAL_REQUESTED:
            run = await get_run(db, "approval-run-1")
            assert run.status is RunStatus.WAITING_APPROVAL
            saw_waiting = True
            await manager.decide(event.payload["approval_id"], True, decided_by="test-operator")

    assert saw_waiting is True
    types = [event.type for event in events]
    assert TraceEventType.TOOL_APPROVAL_RESOLVED in types
    assert types[-1] is TraceEventType.RUN_COMPLETED
    resolved = next(e for e in events if e.type is TraceEventType.TOOL_APPROVAL_RESOLVED)
    assert resolved.payload["approved"] is True
    assert resolved.payload["status"] == "approved"
    assert resolved.payload["decided_by"] == "test-operator"
    assert datetime.fromisoformat(resolved.payload["decided_at"])
    run = await get_run(db, "approval-run-1")
    assert run.status is RunStatus.COMPLETED
    assert run.output == "审批后完成"


# ------------------------------------------------------------- 版本可运行性


async def test_unrunnable_when_tool_missing_from_registry(db, registry, make_runtime):
    await seed_fake_brain(db, script=["ok"])
    await create_test_agent(db, tools=["ghost"])
    runtime = make_runtime()
    with pytest.raises(LubanError) as exc_info:
        await collect(runtime, RunRequest(agent_key="test-agent", user_input="问"))
    assert exc_info.value.code == ErrorCode.AGENT_VERSION_UNRUNNABLE
    assert exc_info.value.details["tool"] == "ghost"


async def test_unrunnable_when_tool_signature_drifted(db, registry, make_runtime):
    register_echo(registry)
    await seed_fake_brain(db, script=["ok"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": "stale-hash"})
    runtime = make_runtime()
    with pytest.raises(LubanError) as exc_info:
        await collect(runtime, RunRequest(agent_key="test-agent", user_input="问"))
    assert exc_info.value.code == ErrorCode.AGENT_VERSION_UNRUNNABLE
    assert exc_info.value.details["tool"] == "echo"


async def test_agent_not_found_raises(db, registry, make_runtime):
    runtime = make_runtime()
    with pytest.raises(LubanError) as exc_info:
        await collect(runtime, RunRequest(agent_key="nope", user_input="问"))
    assert exc_info.value.code == ErrorCode.AGENT_NOT_FOUND


# ------------------------------------------------------------- Trace 七问


async def test_trace_answers_seven_questions(db, registry, make_runtime, monkeypatch):
    descriptor = register_echo(registry)
    await seed_fake_brain(db, script=["占位"], pricing=(1.0, 2.0))
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(
            script=[{"content": "思考", **ECHO_CALL}, "最终答案"], usage=(10, 5)
        ),
    )
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="你好", session_id="sess-1")
    events = await collect(runtime, request)
    run_id = events[0].run_id

    run = await get_run(db, run_id)
    trace_events = await get_events(db, run_id)
    async with db.session() as session:
        trace = await TraceRepository(session).get_trace_by_run(run_id)
        usage_rows = (
            await session.scalars(select(TokenUsageRow).where(TokenUsageRow.run_id == run_id))
        ).all()

    # 一问：谁跑的（哪个 Agent、哪个版本、哪个会话）
    assert (run.agent_key, run.agent_version, run.session_id) == ("test-agent", 1, "sess-1")
    # 二问：用了哪个大脑、哪个模型
    assert run.brain_profile is BrainProfile.SMART
    assert run.model_key == "fake-chat"
    # 三问：输入是什么
    assert run.input["user_input"] == "你好"
    # 四问：输出是什么
    assert run.output == "最终答案"
    # 五问：调了哪些工具、结果如何
    tool_started = [e for e in trace_events if e.type is TraceEventType.TOOL_CALL_STARTED]
    tool_finished = [e for e in trace_events if e.type is TraceEventType.TOOL_CALL_COMPLETED]
    assert [e.payload["tool"] for e in tool_started] == ["echo"]
    assert tool_finished[0].payload["result"] == "echo:hi"
    # 六问：花了多少 token / 多少钱
    assert len(usage_rows) == 2  # 两次模型调用
    assert sum(r.input_tokens + r.output_tokens for r in usage_rows) == 30
    assert run.token_usage.total == 30
    assert run.token_usage.model_calls == 2
    assert run.cost == pytest.approx(2 * (10 / 1000 * 1.0 + 5 / 1000 * 2.0))
    # 七问：终态与时间线
    assert run.status is RunStatus.COMPLETED
    assert run.end_reason is EndReason.FINAL_ANSWER
    assert run.started_at is not None and run.ended_at is not None
    assert trace is not None and trace.status is RunStatus.COMPLETED
    assert trace.ended_at is not None
    assert trace_events[0].type is TraceEventType.RUN_STARTED
    assert trace_events[-1].type is TraceEventType.RUN_COMPLETED


# ------------------------------------------------------------- 观测双写与失败隔离


class StubBackend:
    """记录 start_trace/emit/end_trace 调用序列的 stub 后端。"""

    name = "stub"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def start_trace(self, run):
        self.calls.append(("start_trace", run.id))
        return object()

    def emit(self, handle, event) -> None:
        self.calls.append(("emit", event.type.value))

    def end_trace(self, handle, status, summary=None) -> None:
        self.calls.append(("end_trace", status.value, summary))


class ExplodingBackend(StubBackend):
    name = "exploding"

    def emit(self, handle, event) -> None:
        raise RuntimeError("后端炸了")


async def test_observability_double_write_call_sequence(db, registry, make_runtime):
    descriptor = register_echo(registry)
    await seed_fake_brain(db, script=["想一下", ECHO_CALL, "最终答案"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    stub = StubBackend()
    runtime = make_runtime(observability=stub)
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="你好"))

    assert stub.calls[0][0] == "start_trace"
    end_calls = [c for c in stub.calls if c[0] == "end_trace"]
    assert len(end_calls) == 1
    assert end_calls[0][1] == "completed"
    assert end_calls[0][2]["end_reason"] == EndReason.FINAL_ANSWER.value
    # 双写：每个流出的事件都外发给了后端（drain 后调用集齐）
    emitted = Counter(c[1] for c in stub.calls if c[0] == "emit")
    streamed = Counter(event.type.value for event in events)
    assert emitted == streamed


async def test_observability_backend_failure_does_not_block_run(db, registry, make_runtime):
    await seed_fake_brain(db, script=["最终答案"])
    await create_test_agent(db)
    runtime = make_runtime(observability=ExplodingBackend())
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="问"))
    assert events[-1].type is TraceEventType.RUN_COMPLETED
    run = await get_run(db, events[0].run_id)
    assert run.status is RunStatus.COMPLETED


# ------------------------------------------------------------- hooks 接线


class RecordingHook:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def pre_run(self, ctx) -> None:
        self.calls.append(("pre_run", ctx.run_id))

    async def pre_tool(self, ctx, tool, args) -> None:
        self.calls.append(("pre_tool", tool.key, dict(args)))

    async def post_step(self, ctx, step) -> None:
        self.calls.append(("post_step", step.kind))


async def test_hooks_wired(db, registry, make_runtime):
    descriptor = register_echo(registry)
    await seed_fake_brain(db, script=[ECHO_CALL, "最终答案"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    hook = RecordingHook()
    registry.register_hook(hook)
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="你好"))
    assert events[-1].type is TraceEventType.RUN_COMPLETED

    kinds = [call[0] for call in hook.calls]
    assert kinds.count("pre_run") == 1
    assert ("pre_tool", "echo", {"text": "hi"}) in hook.calls
    # post_step 按步调用：model -> tool -> model
    assert [c[1] for c in hook.calls if c[0] == "post_step"] == ["model", "tool", "model"]


# --------------------------------- MODEL_CALL_COMPLETED 载荷（D2-1 契约决策 2）


async def test_model_call_completed_payload(db, registry, make_runtime):
    """MODEL_CALL_COMPLETED payload 含模型输出 text/tool_calls（录制重放数据源）。"""
    descriptor = register_echo(registry)
    await seed_fake_brain(db, script=[ECHO_CALL, "最终答案"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))

    events = await get_events(db, run.id)
    completed = [e for e in events if e.type is TraceEventType.MODEL_CALL_COMPLETED]
    assert len(completed) == 2
    # 第一次模型调用：工具调用（无文本）
    assert completed[0].payload["text"] == ""
    assert completed[0].payload["tool_calls"] == [
        {"name": "echo", "args": {"text": "hi"}, "id": "c1"}
    ]
    # 第二次模型调用：最终文本（无工具调用）
    assert completed[1].payload["text"] == "最终答案"
    assert completed[1].payload["tool_calls"] == []


# --------------------------------- 事件 schema 一致性（H10）


async def test_model_call_started_payload_schema(db, registry, make_runtime):
    """MODEL_CALL_STARTED payload = {model_key, input:[{role,content}...]}（同一约定）。"""
    descriptor = register_echo(registry)
    await seed_fake_brain(db, script=[ECHO_CALL, "最终答案"])
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))

    events = await get_events(db, run.id)
    started = [e for e in events if e.type is TraceEventType.MODEL_CALL_STARTED]
    assert len(started) == 2
    for event in started:
        assert event.payload["model_key"] == "fake-chat"
    # input 是消息序列：system prompt + user 输入
    first_input = started[0].payload["input"]
    assert first_input[0]["role"] == "system"
    assert first_input[-1] == {"role": "user", "content": "你好"}
    # started 与 completed 的 step_index 一一配对（适配器 pairing 依据）
    completed = [e for e in events if e.type is TraceEventType.MODEL_CALL_COMPLETED]
    assert [e.step_index for e in started] == [e.step_index for e in completed]


async def test_model_call_completed_usage_unknown_explicit(db, registry, make_runtime):
    """模型未回报 usage → 事件 token 值 None + usage_known=False；账本存 NULL 而非 0。"""
    await seed_fake_brain(db, script=["回答"])  # ScriptedFakeChatModel 无 usage_metadata
    await create_test_agent(db)
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))

    events = await get_events(db, run.id)
    completed = [e for e in events if e.type is TraceEventType.MODEL_CALL_COMPLETED]
    assert len(completed) == 1
    assert completed[0].payload["input_tokens"] is None
    assert completed[0].payload["output_tokens"] is None
    assert completed[0].payload["usage_known"] is False
    assert completed[0].payload["model_key"] == "fake-chat"

    async with db.session() as session:
        usage_rows = (
            await session.scalars(select(TokenUsageRow).where(TokenUsageRow.run_id == run.id))
        ).all()
    assert len(usage_rows) == 1
    assert usage_rows[0].input_tokens is None
    assert usage_rows[0].output_tokens is None


async def test_model_call_completed_usage_known_schema(db, registry, make_runtime, monkeypatch):
    """模型回报 usage → 事件带数值 + usage_known=True；账本落实际数值。"""
    await seed_fake_brain(db, script=["占位"])
    await create_test_agent(db)
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=["回答"], usage=(7, 3)),
    )
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))

    events = await get_events(db, run.id)
    completed = [e for e in events if e.type is TraceEventType.MODEL_CALL_COMPLETED]
    assert completed[0].payload["usage_known"] is True
    assert completed[0].payload["input_tokens"] == 7
    assert completed[0].payload["output_tokens"] == 3
    async with db.session() as session:
        usage_rows = (
            await session.scalars(select(TokenUsageRow).where(TokenUsageRow.run_id == run.id))
        ).all()
    assert (usage_rows[0].input_tokens, usage_rows[0].output_tokens) == (7, 3)


async def test_model_override_run_local_injection(db, registry, make_runtime):
    """RunRequest.model_override：Run 局部模型注入，不查不改共享 brain mapping。"""
    await seed_fake_brain(db, script=["共享槽位模型"])
    await create_test_agent(db)
    override = fake_definition(key="override-fake", script=["局部注入模型回答"])
    runtime = make_runtime()
    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="你好", model_override=override)
    )
    assert run.status is RunStatus.COMPLETED
    assert run.output == "局部注入模型回答"
    assert run.model_key == "override-fake"
    # 共享映射未被读取/修改
    async with db.session() as session:
        mapping = await ModelRepository(session).get_brain_mapping("dev", BrainProfile.SMART)
        override_row = await ModelRepository(session).get_model("override-fake")
    assert mapping is not None and mapping.key == "fake-chat"
    assert override_row is None  # 局部注入不落 models 表


async def test_model_override_capability_mismatch_fails(db, registry, make_runtime):
    """model_override 能力不足 → CAPABILITY_MISMATCH 显式 RUN_FAILED，不静默降级。"""
    register_echo(registry)
    await seed_fake_brain(db, script=["共享槽位模型"])
    await create_test_agent(db, tools=["echo"], key="tool-agent")
    weak = fake_definition(
        key="weak-fake",
        script=[],
        capability=ModelCapability(
            chat=True,
            streaming=True,
            tool_calling=False,
            structured_output=True,
            vision=True,
            reasoning=True,
        ),
    )
    runtime = make_runtime()
    events = await collect(
        runtime,
        RunRequest(agent_key="tool-agent", user_input="你好", model_override=weak),
    )
    assert events[-1].type is TraceEventType.RUN_FAILED
    assert events[-1].payload["error"]["code"] == "CAPABILITY_MISMATCH"


# --------------------------------- 错误与事件 payload 的安全数据契约（H07）


async def test_generic_exception_yields_stable_internal_error_without_body(
    db, registry, make_runtime, monkeypatch
):
    """非 LubanError 异常收敛为 INTERNAL_ERROR：SDK/上游正文/URL 不进 run.error/RUN_FAILED。"""

    def _boom(definition):
        raise RuntimeError("connect to https://user:sk-synthetic-secret@api.invalid/v1 failed")

    await seed_fake_brain(db, script=["占位"])  # 映射就位，实际模型由 monkeypatch 接管
    await create_test_agent(db)
    monkeypatch.setattr("lubanagent.runtime.agent_loop.create_chat_model", _boom)
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="你好")
    events = await collect(runtime, request)

    assert events[-1].type is TraceEventType.RUN_FAILED
    error = events[-1].payload["error"]
    assert error["code"] == ErrorCode.INTERNAL_ERROR.value
    assert error["message"] == "内部错误: RuntimeError"
    assert "sk-synthetic-secret" not in json.dumps(events[-1].payload, ensure_ascii=False)

    run = await get_run(db, events[0].run_id)
    assert run.status is RunStatus.FAILED
    assert run.error is not None
    assert "sk-synthetic-secret" not in json.dumps(run.error, ensure_ascii=False)
    db_events = await get_events(db, run.id)
    assert "sk-synthetic-secret" not in json.dumps(
        [e.payload for e in db_events], ensure_ascii=False, default=str
    )


async def test_tool_event_args_redacted_but_handler_executes_with_real_value(
    db, registry, make_runtime
):
    """审计副本脱敏、执行用真实参数：事件里凭证隐藏，handler 收到原值。"""

    @tool(registry=registry, description="带凭证头的工具")
    def cred_tool(auth_header: str, order_id: str) -> str:
        return f"len={len(auth_header)}:{order_id}"

    descriptor = registry.get_tool("cred_tool").descriptor
    secret_header = "Bearer sk-synthetic-secret"
    await seed_fake_brain(
        db,
        script=[
            {
                "tool_calls": [
                    {
                        "name": "cred_tool",
                        "args": {"auth_header": secret_header, "order_id": "A-1"},
                        "id": "c1",
                    }
                ]
            },
            "最终答案",
        ],
    )
    await create_test_agent(
        db,
        tools=["cred_tool"],
        tool_signatures={"cred_tool": descriptor.signature_hash},
    )
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="你好"))

    started = next(e for e in events if e.type is TraceEventType.TOOL_CALL_STARTED)
    assert started.payload["args"]["auth_header"] == "[REDACTED]"
    assert started.payload["args"]["order_id"] == "A-1"
    completed = next(e for e in events if e.type is TraceEventType.TOOL_CALL_COMPLETED)
    # handler 用真实参数执行成功：结果携带真实凭证头长度
    assert completed.payload["result"] == f"len={len(secret_header)}:A-1"

    # DB 审计副本同样脱敏
    db_events = await get_events(db, events[0].run_id)
    db_started = next(e for e in db_events if e.type is TraceEventType.TOOL_CALL_STARTED)
    assert db_started.payload["args"]["auth_header"] == "[REDACTED]"


async def test_approval_requested_args_redacted_but_execution_uses_real_value(
    db, registry, make_runtime
):
    """审批事件 args 为脱敏副本；批准后工具用真实参数执行成功。"""

    @tool(registry=registry, description="带凭证头的高风险工具", approval_required=True)
    def cred_tool(auth_header: str) -> str:
        return "ok"

    descriptor = registry.get_tool("cred_tool").descriptor
    await seed_fake_brain(
        db,
        script=[
            {
                "tool_calls": [
                    {
                        "name": "cred_tool",
                        "args": {"auth_header": "Bearer sk-synthetic-secret"},
                        "id": "c1",
                    }
                ]
            },
            "完成",
        ],
    )
    await create_test_agent(
        db,
        tools=["cred_tool"],
        tool_signatures={"cred_tool": descriptor.signature_hash},
    )
    manager = ApprovalManager()
    runtime = make_runtime(approval_manager=manager)
    request = RunRequest(agent_key="test-agent", user_input="审批", run_id="approval-redact-1")

    events: list[TraceEvent] = []
    async for event in runtime.stream(request):
        events.append(event)
        if event.type is TraceEventType.TOOL_APPROVAL_REQUESTED:
            await manager.decide(event.payload["approval_id"], True, decided_by="op")

    requested = next(e for e in events if e.type is TraceEventType.TOOL_APPROVAL_REQUESTED)
    assert requested.payload["args"] == {"auth_header": "[REDACTED]"}
    completed = next(e for e in events if e.type is TraceEventType.TOOL_CALL_COMPLETED)
    assert completed.payload["result"] == "ok"  # 真实凭证头通过校验并执行
    run = await get_run(db, "approval-redact-1")
    assert run.status is RunStatus.COMPLETED


async def test_model_call_completed_tool_call_args_redacted(db, registry, make_runtime):
    """MODEL_CALL_COMPLETED 的 tool_calls[].args 审计副本脱敏。"""
    descriptor = register_echo(registry)
    await seed_fake_brain(
        db,
        script=[
            {
                "tool_calls": [
                    {"name": "echo", "args": {"text": "Bearer sk-synthetic-secret"}, "id": "c1"}
                ]
            },
            "最终答案",
        ],
    )
    await create_test_agent(db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash})
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))

    events = await get_events(db, run.id)
    completed = [e for e in events if e.type is TraceEventType.MODEL_CALL_COMPLETED]
    assert completed[0].payload["tool_calls"] == [
        {"name": "echo", "args": {"text": "[REDACTED]"}, "id": "c1"}
    ]


async def test_luban_error_details_redacted_in_run_failed(
    db, registry, make_runtime, monkeypatch
):
    """LubanError 的 details 经 redact_value 脱敏后进 run.error/RUN_FAILED。"""

    def _boom(definition):
        raise LubanError(
            ErrorCode.CONFIG_ERROR,
            "模型构造失败",
            details={"api_key": "sk-synthetic-secret", "model_key": "m1"},
        )

    await seed_fake_brain(db, script=["占位"])
    await create_test_agent(db)
    monkeypatch.setattr("lubanagent.runtime.agent_loop.create_chat_model", _boom)
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="你好"))

    assert events[-1].type is TraceEventType.RUN_FAILED
    error = events[-1].payload["error"]
    assert error["code"] == ErrorCode.CONFIG_ERROR.value
    assert error["message"] == "模型构造失败"  # 框架组合的稳定消息保留
    assert error["details"]["api_key"] == "[REDACTED]"
    assert error["details"]["model_key"] == "m1"  # 非秘密字段保留可排查


async def test_tool_failed_error_details_redacted(db, registry, make_runtime):
    """TOOL_CALL_FAILED 事件的 error.details 脱敏，非秘密字段保留。"""

    @tool(registry=registry, description="抛业务错误的工具")
    def failing_tool(order_id: str) -> str:
        raise LubanError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            "下游失败",
            details={"api_key": "sk-synthetic-secret", "order_id": order_id},
        )

    descriptor = registry.get_tool("failing_tool").descriptor
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "failing_tool", "args": {"order_id": "A-1"}, "id": "c1"}]},
            "最终答案",
        ],
    )
    await create_test_agent(
        db,
        tools=["failing_tool"],
        tool_signatures={"failing_tool": descriptor.signature_hash},
    )
    runtime = make_runtime()
    events = await collect(runtime, RunRequest(agent_key="test-agent", user_input="你好"))

    failed = next(e for e in events if e.type is TraceEventType.TOOL_CALL_FAILED)
    assert failed.payload["error"]["details"]["api_key"] == "[REDACTED]"
    assert failed.payload["error"]["details"]["order_id"] == "A-1"
