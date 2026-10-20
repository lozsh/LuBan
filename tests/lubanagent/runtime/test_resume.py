"""缺口 #2：普通 Run 逐步 checkpoint + 通用 resume（run-checkpoint-resume 契约验收）。

覆盖：定向错误路径（终态/无 checkpoint/活跃/子 Run/未启用）、并发串行、
崩溃前缀恢复（核心）、预算账本恢复、审批重放、取消传播、无 checkpointer 零写入。

崩溃现场模拟口径（确定性）：进程崩溃不会走任何 in-process 收尾，库里留下的现场是
「Run 行非终态 + checkpoints 表已有部分 superstep checkpoint」。测试用「首轮完整
执行 → 回退 checkpoint 到指定 superstep → Run 行置 RUNNING」构造等价现场，
避免依赖事件流提前关闭的取消竞态。
"""

import asyncio

from sqlalchemy import delete, select

from lubanagent.core import (
    EndReason,
    ErrorCode,
    LubanError,
    RunStatus,
    TraceEventType,
)
from lubanagent.extension import tool
from lubanagent.persistence import (
    DbApprovalStore,
    LubanCheckpointer,
    RunRepository,
    TraceRepository,
)
from lubanagent.persistence.models import CheckpointRow
from lubanagent.runtime import RunRequest
from lubanagent.tool import ApprovalManager

from .conftest import (
    UsageFakeChatModel,
    budget_boundary,
    create_test_agent,
    register_echo,
    seed_fake_brain,
)

ECHO_CALL = {"tool_calls": [{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]}


# ---------------------------------------------------------------- 现场构造 helpers


async def _rewind_checkpoints(db, run_id: str, *, keep: int) -> None:
    """保留 thread 最早 keep 个 checkpoint（时间有序），删除其余——回退到崩溃前 superstep。

    checkpoint 粒度实测（create_react_agent）：#1 空初始态，#2 用户消息已入通道
    （模型调用未完成），#3 起 逐节点推进。keep=2 = 「首轮模型调用中途崩溃」现场，
    resume 会重放 agent 节点（模型调用）与 tools 节点（工具重放）。
    """
    thread = f"run-{run_id}"
    async with db.session() as session:
        ids = (
            (
                await session.execute(
                    select(CheckpointRow.checkpoint_id)
                    .where(CheckpointRow.thread_id == thread)
                    .order_by(CheckpointRow.checkpoint_id.asc())
                )
            )
            .scalars()
            .all()
        )
        stale = list(ids[keep:])  # 删除第 keep+1 个起的所有 checkpoint，保留最早 keep 个
        if stale:
            await session.execute(
                delete(CheckpointRow).where(
                    CheckpointRow.thread_id == thread,
                    CheckpointRow.checkpoint_id.in_(stale),
                )
            )


async def _mark_resumable(db, run_id: str) -> None:
    """Run 行置回 RUNNING 并清终态字段（进程崩溃未走 finalize 的库内现场）。"""
    async with db.session() as session:
        run = await RunRepository(session).get_run(run_id)
        run.status = RunStatus.RUNNING
        run.output = None
        run.ended_at = None
        await RunRepository(session).update_run(run)


async def _get_run(db, run_id: str):
    async with db.session() as session:
        return await RunRepository(session).get_run(run_id)


async def _first_error(agen) -> LubanError:
    """async generator 的首个 anext：resume 校验类错误在流开始前抛出。"""
    try:
        await agen.__anext__()
    except LubanError as exc:
        return exc
    raise AssertionError("stream_resume 未在流开始前抛错")


# ------------------------------------------------------------ 验收 1：定向错误路径


async def test_resume_error_paths(db, registry, make_runtime):
    await seed_fake_brain(db, script=["答案"])
    await create_test_agent(db)
    checkpointer = LubanCheckpointer(db)
    runtime = make_runtime(checkpointer=checkpointer)
    bare = make_runtime()  # 未注入 checkpointer

    # run 不存在 → RUN_NOT_FOUND
    exc = await _first_error(runtime.stream_resume("ghost"))
    assert exc.code is ErrorCode.RUN_NOT_FOUND

    # 未启用 checkpointer → CONFIG_ERROR（不静默）
    exc = await _first_error(bare.stream_resume("whatever"))
    assert exc.code is ErrorCode.CONFIG_ERROR

    # 终态拒绝 → VALIDATION_ERROR
    done = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="你好", run_id="done-run")
    )
    assert done.status is RunStatus.COMPLETED
    exc = await _first_error(runtime.stream_resume("done-run"))
    assert exc.code is ErrorCode.VALIDATION_ERROR
    assert "终态" in exc.message

    # 无 checkpoint 拒绝 → RUN_NOT_FOUND + reason=no_checkpoint
    await runtime.run(
        RunRequest(agent_key="test-agent", user_input="你好", run_id="nocp-run")
    )
    await _rewind_checkpoints(db, "nocp-run", keep=0)
    await _mark_resumable(db, "nocp-run")
    exc = await _first_error(runtime.stream_resume("nocp-run"))
    assert exc.code is ErrorCode.RUN_NOT_FOUND
    assert exc.details["reason"] == "no_checkpoint"

    # 子 Run 拒绝 → VALIDATION_ERROR（生命周期归父编排；直接建档伪造非终态子 Run）
    from datetime import UTC, datetime

    from lubanagent.core import BrainProfile, Run

    now = datetime.now(UTC)
    sub = Run(
        id="sub-run",
        agent_key="test-agent",
        agent_version=1,
        parent_run_id="parent-x",
        status=RunStatus.RUNNING,
        input={"user_input": "你好", "variables": {}},
        brain_profile=BrainProfile.SMART,
        model_key="fake-chat",
        created_at=now,
        started_at=now,
    )
    async with db.session() as session:
        await RunRepository(session).create_run(sub)
    exc = await _first_error(runtime.stream_resume("sub-run"))
    assert exc.code is ErrorCode.VALIDATION_ERROR
    assert "子 Run" in exc.message


# ------------------------------------------------------------ 验收 1：活跃拒绝


async def test_resume_active_run_rejected(db, registry, make_runtime):
    @tool(registry=registry, description="慢工具")
    async def slow(text: str) -> str:
        await asyncio.sleep(0.2)
        return f"slow:{text}"

    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow", "args": {"text": "x"}, "id": "c1"}]},
            "完成",
        ],
    )
    await create_test_agent(db, tools=["slow"])
    runtime = make_runtime(checkpointer=LubanCheckpointer(db))
    run_id = "active-run"

    resume_error: LubanError | None = None

    async def probe(event):
        nonlocal resume_error
        if event.type is TraceEventType.TOOL_CALL_STARTED and resume_error is None:
            resume_error = await _first_error(runtime.stream_resume(run_id))

    async for ev in runtime.stream(
        RunRequest(agent_key="test-agent", user_input="你好", run_id=run_id)
    ):
        await probe(ev)
        if resume_error is not None:
            await runtime.cancel(run_id)  # 收尾清理

    assert resume_error is not None
    assert resume_error.code is ErrorCode.VALIDATION_ERROR
    assert "执行中" in resume_error.message


# ------------------------------------------------------------ 验收 2：并发串行


async def test_resume_concurrent_serialized(db, registry, make_runtime):
    @tool(registry=registry, description="慢工具")
    async def slow(text: str) -> str:
        await asyncio.sleep(0.3)
        return f"slow:{text}"

    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow", "args": {"text": "x"}, "id": "c1"}]},
            "长回答",
        ],
    )
    await create_test_agent(db, tools=["slow"])
    checkpointer = LubanCheckpointer(db)
    runtime = make_runtime(checkpointer=checkpointer)
    run_id = "serial-run"
    await runtime.run(RunRequest(agent_key="test-agent", user_input="你好", run_id=run_id))
    await _rewind_checkpoints(db, run_id, keep=1)
    await _mark_resumable(db, run_id)

    first = asyncio.create_task(_collect_all(runtime, run_id))
    await asyncio.sleep(0.05)  # first 已持锁进入续跑
    second_error: LubanError | None = None

    async def second() -> None:
        nonlocal second_error
        second_error = await _first_error(runtime.stream_resume(run_id))

    second_task = asyncio.create_task(second())
    await asyncio.wait_for(first, 10)
    await asyncio.wait_for(second_task, 10)

    # second 是「等锁后按终态拒绝」，不是「活跃拒绝」→ 证明串行而非并发穿透
    assert second_error is not None
    assert second_error.code is ErrorCode.VALIDATION_ERROR
    assert "终态" in second_error.message
    assert "执行中" not in second_error.message
    run = await _get_run(db, run_id)
    assert run.status is RunStatus.COMPLETED  # 只推进了一个终态


async def _collect_all(runtime, run_id: str):
    return [event async for event in runtime.stream_resume(run_id)]


# ------------------------------------------------------------ 验收 3：崩溃前缀恢复（核心）


async def test_crash_prefix_recovery(db, registry, make_runtime):
    register_echo(registry)
    await seed_fake_brain(db, script=[ECHO_CALL, "首轮答案"])
    await create_test_agent(db, tools=["echo"])
    checkpointer = LubanCheckpointer(db)
    runtime1 = make_runtime(checkpointer=checkpointer)
    run_id = "crash-run"

    run = await runtime1.run(
        RunRequest(agent_key="test-agent", user_input="你好", run_id=run_id)
    )
    assert run.status is RunStatus.COMPLETED
    assert run.output == "首轮答案"

    # 构造崩溃现场：回退到第 2 个 checkpoint（用户消息已入通道、首轮模型调用未完成），Run 行 RUNNING
    await _rewind_checkpoints(db, run_id, keep=2)
    await _mark_resumable(db, run_id)

    # 「重启后」新 runtime 实例（同库）：null-resume 续跑
    runtime2 = make_runtime(checkpointer=checkpointer)
    events = await _collect_all(runtime2, run_id)

    resumed = await _get_run(db, run_id)
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.output == "首轮答案"  # 模型脚本自库重载重放
    assert any(e.type is TraceEventType.RUN_RESUMED for e in events)

    # trace 连续性：同一 trace 行追加，无第二条 trace / 无重复 RUN_STARTED
    async with db.session() as session:
        trace_repo = TraceRepository(session)
        trace = await trace_repo.get_trace_by_run(run_id)
        db_types = [e.type for e in await trace_repo.list_events(run_id)]
    assert trace is not None
    assert db_types.count(TraceEventType.RUN_STARTED) == 1
    assert db_types.count(TraceEventType.RUN_RESUMED) == 1
    assert db_types.count(TraceEventType.RUN_COMPLETED) == 2  # 首轮 + 续跑各一条
    # 工具从 checkpoint 重放（副作用重复执行——契约边界 1 的可观测面）
    assert db_types.count(TraceEventType.TOOL_CALL_COMPLETED) == 2


# ------------------------------------------------------------ 验收 4：预算账本恢复


async def test_resume_budget_recovery(db, registry, make_runtime, monkeypatch):
    """首轮已消费 N token 后中断：resume 的 guard 以 N 初始化，不重复放水。"""
    await seed_fake_brain(db, script=[ECHO_CALL, "长回答"])
    register_echo(registry)
    # 两个 agent 差异仅在预算：首轮两步烧 30 token；resume 从用户消息 checkpoint 重放，
    # 再烧两步 30（模型调用 ×2），共 60
    await create_test_agent(
        db, key="rich-agent", tools=["echo"], boundary=budget_boundary(max_tokens=60)
    )  # 30 + 30 恰好不超
    await create_test_agent(
        db, key="poor-agent", tools=["echo"], boundary=budget_boundary(max_tokens=50)
    )  # 60 > 50 → 必熔断

    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(
            script=[ECHO_CALL, "长回答"], usage=(10, 5)
        ),
    )
    checkpointer = LubanCheckpointer(db)
    runtime = make_runtime(checkpointer=checkpointer)

    # 场景 A：余量充足 → 续跑 COMPLETED
    run_id = "budget-ok"
    run = await runtime.run(RunRequest(agent_key="rich-agent", user_input="你好", run_id=run_id))
    assert run.status is RunStatus.COMPLETED
    await _rewind_checkpoints(db, run_id, keep=2)
    await _mark_resumable(db, run_id)
    events = await _collect_all(runtime, run_id)
    resumed = await _get_run(db, run_id)
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.output == "长回答"
    assert any(e.type is TraceEventType.RUN_RESUMED for e in events)

    # 场景 B：剩余 < 一次调用 → BUDGET_EXCEEDED（账本恢复不放水）
    run_id = "budget-poor"
    run = await runtime.run(RunRequest(agent_key="poor-agent", user_input="你好", run_id=run_id))
    assert run.status is RunStatus.COMPLETED
    await _rewind_checkpoints(db, run_id, keep=2)
    await _mark_resumable(db, run_id)
    await _collect_all(runtime, run_id)
    resumed = await _get_run(db, run_id)
    assert resumed.status is RunStatus.FAILED
    assert resumed.end_reason is EndReason.BUDGET_EXCEEDED
    assert resumed.error is not None
    assert resumed.error["code"] == ErrorCode.BUDGET_EXCEEDED.value


# ------------------------------------------------------------ 验收 5：审批重放


async def test_resume_approval_replay(db, registry, make_runtime):
    descriptor = register_echo(registry, approval_required=True)
    await seed_fake_brain(db, script=[ECHO_CALL, "首轮答案"])
    await create_test_agent(
        db, tools=["echo"], tool_signatures={"echo": descriptor.signature_hash}
    )
    checkpointer = LubanCheckpointer(db)
    manager1 = ApprovalManager(store=DbApprovalStore(db))
    runtime1 = make_runtime(checkpointer=checkpointer, approval_manager=manager1)
    run_id = "approval-resume-run"

    async def decide_round1(event):
        if event.type is TraceEventType.TOOL_APPROVAL_REQUESTED:
            await manager1.decide(event.payload["approval_id"], True, decided_by="op-1")

    events1 = []
    async for ev in runtime1.stream(
        RunRequest(agent_key="test-agent", user_input="审批", run_id=run_id)
    ):
        events1.append(ev)
        await decide_round1(ev)
    assert any(e.type is TraceEventType.RUN_COMPLETED for e in events1)

    # 崩溃现场 + 崩溃前遗留的 pending 审批（审计保留，续跑不消费它）
    await _rewind_checkpoints(db, run_id, keep=2)
    await _mark_resumable(db, run_id)
    manager2 = ApprovalManager(store=DbApprovalStore(db))
    leftover = await manager2.create_request(run_id, descriptor, {"text": "遗留请求"})

    runtime2 = make_runtime(checkpointer=checkpointer, approval_manager=manager2)

    async def decide_round2(event):
        if (
            event.type is TraceEventType.TOOL_APPROVAL_REQUESTED
            and event.payload["approval_id"] != leftover.id
        ):
            await manager2.decide(event.payload["approval_id"], True, decided_by="op-2")

    events2 = await _collect_all_with(runtime2, run_id, decide_round2)
    resumed = await _get_run(db, run_id)
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.output == "首轮答案"

    # 重放工具 → 新审批请求落库并被 decide；遗留 pending 原样保留（审计完整）
    new_ids = {
        e.payload["approval_id"]
        for e in events2
        if e.type is TraceEventType.TOOL_APPROVAL_REQUESTED
    }
    assert new_ids and all(aid != leftover.id for aid in new_ids)
    pendings = await manager2.pending(run_id=run_id)
    assert [p.id for p in pendings] == [leftover.id]


async def _collect_all_with(runtime, run_id: str, on_event):
    events = []
    async for event in runtime.stream_resume(run_id):
        events.append(event)
        await on_event(event)
    return events


# ------------------------------------------------------------ 验收 6：取消传播


async def test_resume_cancel_propagation(db, registry, make_runtime):
    @tool(registry=registry, description="慢工具")
    async def slow(text: str) -> str:
        await asyncio.sleep(0.2)
        return f"slow:{text}"

    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow", "args": {"text": "x"}, "id": "c1"}]},
            "长回答",
        ],
    )
    await create_test_agent(db, tools=["slow"])
    runtime = make_runtime(checkpointer=LubanCheckpointer(db))
    run_id = "cancel-resume-run"
    await runtime.run(RunRequest(agent_key="test-agent", user_input="你好", run_id=run_id))
    await _rewind_checkpoints(db, run_id, keep=2)
    await _mark_resumable(db, run_id)

    async def trigger_cancel(event):
        if event.type is TraceEventType.TOOL_CALL_STARTED:
            await runtime.cancel(run_id)

    events = await asyncio.wait_for(
        _collect_all_with(runtime, run_id, trigger_cancel), 10
    )
    assert events[-1].type is TraceEventType.RUN_CANCELLED
    run = await _get_run(db, run_id)
    assert run.status is RunStatus.CANCELLED
    assert run.end_reason is EndReason.CANCELLED


# ------------------------------------------------------------ 验收 7：无 checkpointer 零写入


async def test_run_without_checkpointer_writes_no_checkpoints(db, registry, make_runtime):
    await seed_fake_brain(db, script=["答案"])
    await create_test_agent(db)
    runtime = make_runtime()  # checkpointer=None：现有行为零变化
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))
    assert run.status is RunStatus.COMPLETED
    async with db.session() as session:
        rows = (await session.execute(select(CheckpointRow))).scalars().all()
    assert rows == []
