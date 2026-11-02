"""U1 环集成验收：FakeLLM 剧本走真实 Run——异步工具占位/收取/终止/清理/审批前置。"""

import asyncio

from lubanagent.core import BoundaryConfig, RiskLevel, RunStatus, TraceEventType
from lubanagent.extension import Registry, tool
from lubanagent.persistence import RunRepository, TraceRepository
from lubanagent.runtime import RunRequest
from lubanagent.tool import ApprovalManager

from .conftest import create_test_agent, seed_fake_brain


def register_slow_async_tool(
    registry: Registry, *, gate: asyncio.Event | None = None, instant: bool = False
):
    """注册 async 工具：instant=即时返回（无等待，确定性先于模型下一轮完成）；
    gate 给定时挂起直到放行；否则 0.01s 返回。"""

    @tool(registry=registry, description="慢后台工具", execution_mode="async")
    async def slow_task() -> str:
        if gate is not None:
            await gate.wait()
        if not instant:
            await asyncio.sleep(0.01)
        return "后台结果"

    return registry.get_tool("slow_task").descriptor


def types_of(events):
    return [e.type for e in events]


async def collect(stream):
    return [e async for e in stream]


async def get_run(db, run_id):
    async with db.session() as session:
        return await RunRepository(session).get_run(run_id)


async def test_async_tool_disabled_falls_back_to_sync(db, registry, make_runtime):
    """开关关（默认）→ async 声明按同步路径执行，无 TOOL_TASK_* 事件（零行为变化）。"""
    register_slow_async_tool(registry)
    await seed_fake_brain(db, script=[{"tool_calls": [{"name": "slow_task", "args": {}}]}, "完成"])
    await create_test_agent(db, tools=["slow_task"])
    runtime = make_runtime()  # async_tools 默认 None → Settings.tool_async_enabled=False
    events = await collect(runtime.stream(RunRequest(agent_key="test-agent", user_input="跑")))
    assert events[-1].type is TraceEventType.RUN_COMPLETED
    assert TraceEventType.TOOL_TASK_ACCEPTED not in types_of(events)
    completed = next(e for e in events if e.type is TraceEventType.TOOL_CALL_COMPLETED)
    assert completed.payload["result"] == "后台结果"  # 同步拿到结果直接回喂


async def test_async_happy_path_placeholder_then_result(db, registry, make_runtime):
    """开关开：占位返回 task-1 → 模型 task_result 轮询收取终态结果 → COMPLETED。

    instant 工具（无等待）+ 两次轮询：poll1 可能拿到 running（supervisor 置状态
    晚 exec_task 一拍）也可能拿到 completed，poll2 经历整轮图往返后必然已见终态
    ——断言钉在最后一跳（修复严审 blocking #1 的赛跑竞态，容忍调度浮动）。
    """
    register_slow_async_tool(registry, instant=True)
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow_task", "args": {}}]},
            {"tool_calls": [{"name": "task_result", "args": {"task_id": "task-1"}}]},
            {"tool_calls": [{"name": "task_result", "args": {"task_id": "task-1"}}]},
            "已收取后台结果",
        ],
    )
    await create_test_agent(db, tools=["slow_task"])
    runtime = make_runtime(async_tools=True)
    run_id = "async-run-1"
    events = await collect(
        runtime.stream(RunRequest(agent_key="test-agent", user_input="跑", run_id=run_id))
    )
    assert events[-1].type is TraceEventType.RUN_COMPLETED
    # 后台事件与主循环事件允许乱序（契约：按 created_at 排序）——断言多重集不断言顺序
    task_types = [e.type for e in events if e.type.value.startswith("tool_task")]
    assert sorted(t.value for t in task_types) == [
        "tool_task_accepted",
        "tool_task_completed",
    ]
    accepted = next(e for e in events if e.type is TraceEventType.TOOL_TASK_ACCEPTED)
    assert accepted.payload == {"tool": "slow_task", "task_id": "task-1"}
    # 生命周期由 task 事件接管：slow_task 本体不再发 TOOL_CALL_COMPLETED
    # （task_result 伴生工具自身是同步桥接，照常发 COMPLETED）
    slow_completed = [
        e
        for e in events
        if e.type is TraceEventType.TOOL_CALL_COMPLETED and e.payload.get("tool") == "slow_task"
    ]
    assert slow_completed == []
    # 最后一跳轮询必为终态结果（非 running 视图）；poll1 容忍 running/completed
    polls = [
        e
        for e in events
        if e.type is TraceEventType.TOOL_CALL_COMPLETED and e.payload.get("tool") == "task_result"
    ]
    assert len(polls) == 2
    assert polls[0].payload["result"]["status"] in ("running", "completed")
    assert polls[1].payload["result"] == {
        "status": "completed",
        "task_id": "task-1",
        "result": "后台结果",
    }
    # 模型收走了结果 → 无 abandoned
    assert events[-1].payload.get("abandoned_tasks") is None
    async with db.session() as session:
        persisted = await TraceRepository(session).list_events(run_id)
    assert any(e.type is TraceEventType.TOOL_TASK_COMPLETED for e in persisted)


async def test_abandoned_tasks_on_finalize(db, registry, make_runtime):
    """模型不收取直接回答 → 终态 payload 带 abandoned_tasks，清理事件在终态前。

    gate 挂起保证 finalize 时任务仍在跑（确定性，不赌 0.01s 完成时序）。
    """
    gate = asyncio.Event()
    register_slow_async_tool(registry, gate=gate)
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow_task", "args": {}}]},
            {"tool_calls": [{"name": "task_result", "args": {"task_id": "task-1"}}]},
            "不收取直接回答",
        ],
    )
    await create_test_agent(db, tools=["slow_task"])
    runtime = make_runtime(async_tools=True)
    events = await collect(
        runtime.stream(RunRequest(agent_key="test-agent", user_input="跑", run_id="abandon-1"))
    )
    assert events[-1].type is TraceEventType.RUN_COMPLETED
    assert events[-1].payload["abandoned_tasks"] == ["task-1"]
    cancelled = next(e for e in events if e.type is TraceEventType.TOOL_TASK_CANCELLED)
    assert cancelled.payload["reason"] == "run_finalized"
    # gate 关死 ⇒ 模型的 task_result 轮询拿到的一定是 running 视图（确定性）
    poll = next(
        e
        for e in events
        if e.type is TraceEventType.TOOL_CALL_COMPLETED and e.payload.get("tool") == "task_result"
    )
    assert poll.payload["result"]["status"] == "running"


async def test_run_cancel_propagates_to_background_task(db, registry, make_runtime):
    """Run 中途取消 → 后台任务收敛 cancelled，Run 落 CANCELLED 终态。"""
    gate = asyncio.Event()
    register_slow_async_tool(registry, gate=gate)
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow_task", "args": {}}]},
            "这步会被取消打断",
        ],
    )
    await create_test_agent(db, tools=["slow_task"])
    runtime = make_runtime(async_tools=True)
    run_id = "cancel-async-1"
    events = []
    async for event in runtime.stream(
        RunRequest(agent_key="test-agent", user_input="跑", run_id=run_id)
    ):
        events.append(event)
        if event.type is TraceEventType.TOOL_TASK_ACCEPTED:
            await runtime.cancel(run_id)
    assert events[-1].type is TraceEventType.RUN_CANCELLED
    cancelled = [e for e in events if e.type is TraceEventType.TOOL_TASK_CANCELLED]
    assert cancelled and cancelled[-1].payload["reason"] == "run_cancelled"
    run = await get_run(db, run_id)
    assert run.status is RunStatus.CANCELLED


async def test_model_can_stop_task(db, registry, make_runtime):
    """模型 task_stop 终止挂起任务 → cancelled 事件；收取后不算 abandoned。"""
    gate = asyncio.Event()
    register_slow_async_tool(registry, gate=gate)
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow_task", "args": {}}]},
            {"tool_calls": [{"name": "task_stop", "args": {"task_id": "task-1"}}]},
            {"tool_calls": [{"name": "task_result", "args": {"task_id": "task-1"}}]},
            "已终止并确认",
        ],
    )
    await create_test_agent(db, tools=["slow_task"])
    runtime = make_runtime(async_tools=True)
    events = await collect(
        runtime.stream(RunRequest(agent_key="test-agent", user_input="跑", run_id="stop-1"))
    )
    assert events[-1].type is TraceEventType.RUN_COMPLETED
    assert events[-1].payload.get("abandoned_tasks") is None
    stopped = [e for e in events if e.type is TraceEventType.TOOL_TASK_CANCELLED]
    assert stopped and stopped[0].payload["reason"] == "stopped"


async def test_approval_gates_before_spawn(db, registry, make_runtime):
    """HIGH 风险 async 工具：审批事件先于占位事件（审批不进后台协程）；批准后正常收取。"""
    @tool(
        registry=registry,
        description="高风险后台工具",
        execution_mode="async",
        risk=RiskLevel.HIGH,
    )
    async def risky_task() -> str:
        return "高风险结果"

    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "risky_task", "args": {}}]},
            {"tool_calls": [{"name": "task_result", "args": {"task_id": "task-1"}}]},
            "审批后完成",
        ],
    )
    await create_test_agent(db, tools=["risky_task"])
    manager = ApprovalManager()
    runtime = make_runtime(async_tools=True, approval_manager=manager)
    events = []
    async for event in runtime.stream(
        RunRequest(agent_key="test-agent", user_input="跑", run_id="approval-async-1")
    ):
        events.append(event)
        if event.type is TraceEventType.TOOL_APPROVAL_REQUESTED:
            await manager.decide(event.payload["approval_id"], True, decided_by="t")
    types = types_of(events)
    assert types.index(TraceEventType.TOOL_APPROVAL_REQUESTED) < types.index(
        TraceEventType.TOOL_TASK_ACCEPTED
    )
    assert types[-1] is TraceEventType.RUN_COMPLETED


async def test_approval_rejection_spawns_nothing(db, registry, make_runtime):
    """审批拒绝 → 无 TOOL_TASK_ACCEPTED、无后台任务，拒绝原因回喂模型照常终态。"""
    @tool(
        registry=registry,
        description="高风险后台工具",
        execution_mode="async",
        risk=RiskLevel.HIGH,
    )
    async def risky_task() -> str:
        return "不该执行"

    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "risky_task", "args": {}}]},
            "收到拒绝，向用户说明",
        ],
    )
    await create_test_agent(db, tools=["risky_task"])
    manager = ApprovalManager()
    runtime = make_runtime(async_tools=True, approval_manager=manager)
    events = []
    async for event in runtime.stream(
        RunRequest(agent_key="test-agent", user_input="跑", run_id="approval-async-2")
    ):
        events.append(event)
        if event.type is TraceEventType.TOOL_APPROVAL_REQUESTED:
            await manager.decide(event.payload["approval_id"], False, decided_by="t")
    assert TraceEventType.TOOL_TASK_ACCEPTED not in types_of(events)
    assert events[-1].type is TraceEventType.RUN_COMPLETED  # 拒绝回喂不炸循环
    failed = [e for e in events if e.type is TraceEventType.TOOL_CALL_FAILED]
    assert failed and failed[0].payload["error"]["details"]["decision"] == "rejected"


async def test_placeholder_counts_as_step(db, registry, make_runtime):
    """占位即一步：每次 spawn 走 bridge 的步数推进（与同步工具同一代码路径）。

    断言确定性不变量而非绝对索引——图推进与事件消费解耦，MODEL_CALL_COMPLETED
    的消费可能晚于下一个工具节点（同步工具路径同样如此），绝对 step_index 依
    事件消费时机浮动；契约点是「bridge 对占位递增 step_state」，由两次 spawn
    均被接受 + 索引单调不减刻画。
    """
    register_slow_async_tool(registry, instant=True)
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow_task", "args": {}}]},
            {"tool_calls": [{"name": "slow_task", "args": {}}]},
            "两次都跑完了",
        ],
    )
    await create_test_agent(
        db, tools=["slow_task"], boundary=BoundaryConfig(max_steps=16)
    )
    runtime = make_runtime(async_tools=True)
    events = await collect(
        runtime.stream(RunRequest(agent_key="test-agent", user_input="跑", run_id="steps-1"))
    )
    assert events[-1].type is TraceEventType.RUN_COMPLETED
    accepted = [e for e in events if e.type is TraceEventType.TOOL_TASK_ACCEPTED]
    assert len(accepted) == 2
    assert accepted[0].step_index <= accepted[1].step_index


async def test_executor_split_equivalence(db, registry):
    """executor 拆分等价性：execute = preflight + launch（同一输入同结果）。"""
    from lubanagent.tool.executor import ToolExecutor

    executor = ToolExecutor()
    descriptor = register_slow_async_tool(registry)
    registered = registry.get_tool(descriptor.key)
    validated = await executor.preflight(registered, {}, ctx=None)
    assert validated == {}
    result = await executor.launch(registered, validated, ctx=None)
    assert result.success and result.value == "后台结果"
    whole = await executor.execute(registered, {}, ctx=None)
    assert (whole.success, whole.value) == (result.success, result.value)
