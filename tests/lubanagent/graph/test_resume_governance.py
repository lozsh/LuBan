"""interrupt → resume 的父级治理回接（风险 R2）：

- 预算池共享：resume 后与中断前共用同一 BudgetGuard，消耗累计不重置、撞线照常熔断；
- 取消链共享：父 token 取消能传播到恢复后的 Run（节点边界抛 RunCancelledError）；
- 显式 parent_ctx 优先（跨进程/父 Run 已终结场景由调用方重建绑定）；
- 无绑定可用时如实降级（新 runner 实例 ≈ 跨进程重启），不假恢复；
- 终态后重复 resume 显式拒绝、并行 resume 串行边界、副作用不重复执行（H12）。
"""

import asyncio

import pytest

from lubanagent.core import (
    BrainProfile,
    Budget,
    BudgetExceededError,
    EndReason,
    ErrorCode,
    LubanError,
    RunCancelledError,
    RunContext,
    RunStatus,
)
from lubanagent.graph import GraphBuilder
from lubanagent.persistence import Database, RunRepository
from lubanagent.runtime import BudgetGuard, CancellationToken

from .conftest import UsageFakeChatModel, create_test_agent, seed_fake_brain


def _parent_ctx(**metadata_and_fields) -> RunContext:
    metadata = metadata_and_fields.pop("metadata", {})
    return RunContext(
        run_id="parent-run",
        agent_key="parent-agent",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        metadata=metadata,
        **metadata_and_fields,
    )


def _guard_ctx(guard: BudgetGuard) -> RunContext:
    return _parent_ctx(metadata={"budget_guard": guard})


def _token_ctx(token: CancellationToken) -> RunContext:
    return _parent_ctx(metadata={"cancellation_token": token})


def _patch_usage_model(monkeypatch, usage=(10, 10)):
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=["长回答", "再回答"], usage=usage),
    )


async def _seed_agent_flow(db: Database, monkeypatch) -> "object":
    """agent 节点（烧预算）→ human 节点（中断）→ agent 节点（再烧预算）。"""
    await seed_fake_brain(db, script=["长回答", "再回答"])
    await create_test_agent(db, key="chat-agent")
    _patch_usage_model(monkeypatch, usage=(10, 10))
    return (
        GraphBuilder("rg")
        .agent("ask", "chat-agent")
        .human("approve", prompt="需人工确认")
        .agent("after", "chat-agent")
        .entry("ask")
        .edge("ask", "approve")
        .edge("approve", "after")
        .build()
    )


async def _runs(db: Database, agent_key: str):
    async with db.session() as session:
        return await RunRepository(session).list_runs(agent_key)


async def test_resume_accumulates_parent_budget_pool(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """共享 guard 未撞线：中断前烧 20，resume 后再烧 20 → 同一池累计 40（不重置）。"""
    flow = await _seed_agent_flow(db, monkeypatch)
    guard = BudgetGuard(Budget(max_tokens=1000), timeout_seconds=300.0)
    runner = make_runner(runtime=make_runtime())

    first = await runner.run(flow, {"input": "hi"}, parent_ctx=_guard_ctx(guard))
    assert first.status == "interrupted"
    assert guard.usage.total == 20  # 中断前已消耗

    resumed = await runner.resume(flow, first.thread_id, {"approved": True})

    assert resumed.status == "completed"
    assert resumed.run_id == first.run_id
    assert guard.usage.total == 40  # resume 累计进同一池，不重置


async def test_resume_budget_enforcement_uses_accumulated_usage(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """池累计后撞线熔断：max_tokens=30，中断前 20，resume 再烧 20 → 40 > 30 → BUDGET_EXCEEDED。"""
    flow = await _seed_agent_flow(db, monkeypatch)
    guard = BudgetGuard(Budget(max_tokens=30), timeout_seconds=300.0)
    runner = make_runner(runtime=make_runtime())
    ctx = _parent_ctx(metadata={"budget_guard": guard})

    first = await runner.run(flow, {"input": "hi"}, parent_ctx=ctx)
    assert first.status == "interrupted"

    with pytest.raises(BudgetExceededError):
        await runner.resume(flow, first.thread_id, {"approved": True})

    assert guard.usage.total == 40  # 若 resume 重置了池，第二次消费不会撞 30 的线
    runs = await _runs(db, "graph:rg")
    assert runs[0].status == RunStatus.FAILED
    assert runs[0].end_reason == EndReason.BUDGET_EXCEEDED


async def test_resume_cancel_propagates_from_parent_token(make_runner, db: Database):
    """父 token 在中断期间取消 → resume 后节点边界抛 RunCancelledError → Run 终态 CANCELLED。"""
    token = CancellationToken()
    flow = (
        GraphBuilder("rg-cancel")
        .step("a", lambda s: {"a": 1})
        .human("approve", prompt="确认")
        .step("b", lambda s: {"b": 2})
        .entry("a")
        .edge("a", "approve")
        .edge("approve", "b")
        .build()
    )
    runner = make_runner()
    first = await runner.run(flow, {}, parent_ctx=_token_ctx(token))
    assert first.status == "interrupted"

    token.cancel()  # 人工等待期间父 Run 被取消

    with pytest.raises(RunCancelledError):
        await runner.resume(flow, first.thread_id, "ok")

    runs = await _runs(db, "graph:rg-cancel")
    assert runs[0].status == RunStatus.CANCELLED
    assert runs[0].parent_run_id == "parent-run"  # 父子关系不因 resume 断链


async def test_resume_explicit_parent_ctx_takes_precedence(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """显式传入 parent_ctx 优先于冻结绑定：调用方持新 guard 重建（跨进程重启的标准路径）。"""
    flow = await _seed_agent_flow(db, monkeypatch)
    runner = make_runner(runtime=make_runtime())
    loose = BudgetGuard(Budget(), timeout_seconds=300.0)
    first = await runner.run(flow, {"input": "hi"}, parent_ctx=_guard_ctx(loose))
    assert first.status == "interrupted"

    tight = BudgetGuard(Budget(max_tokens=15), timeout_seconds=300.0)
    with pytest.raises(BudgetExceededError):
        await runner.resume(
            flow,
            first.thread_id,
            {"approved": True},
            parent_ctx=_guard_ctx(tight),
        )
    assert tight.usage.total == 20  # 生效的是显式 ctx 的 guard


async def test_resume_without_binding_degrades_gracefully(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """无绑定可用（新 runner 实例 ≈ 跨进程重启）→ 如实降级：不 guard、不熔断，流程照常走完。"""
    flow = await _seed_agent_flow(db, monkeypatch)
    guard = BudgetGuard(Budget(max_tokens=30), timeout_seconds=300.0)
    runner = make_runner(runtime=make_runtime())
    first = await runner.run(flow, {"input": "hi"}, parent_ctx=_guard_ctx(guard))
    assert first.status == "interrupted"

    # 换新 runner 实例：内存绑定丢失（跨进程重启等价场景），且调用方未传 parent_ctx
    fresh_runner = make_runner(runtime=make_runtime())
    resumed = await fresh_runner.resume(flow, first.thread_id, {"approved": True})

    assert resumed.status == "completed"  # 不崩溃、不假熔断
    assert guard.usage.total == 20  # 原 guard 不再被续写（降级边界，如实呈现）


# ------------------------------------------------------------ H12：幂等/竞态/重启恢复


def _counting_flow(name: str):
    """计数节点 → 人工节点 → 计数节点：副作用计数器断言 resume 不重复执行。"""
    counter = {"n": 0}

    def counting(state):
        counter["n"] += 1
        return {"n": counter["n"]}

    flow = (
        GraphBuilder(name)
        .step("count", counting)
        .human("approve", prompt="确认")
        .step("count2", counting)
        .entry("count")
        .edge("count", "approve")
        .edge("approve", "count2")
        .build()
    )
    return flow, counter


async def test_repeated_resume_after_terminal_rejected(make_runner, db: Database):
    """H12：终态后重复 resume 显式拒绝——副作用不重复执行，终态不被改写（幂等边界）。"""
    flow, counter = _counting_flow("idem")
    runner = make_runner()
    first = await runner.run(flow, {})
    assert first.status == "interrupted"

    resumed = await runner.resume(flow, first.thread_id, {"approved": True})
    assert resumed.status == "completed"
    assert counter["n"] == 2  # count + count2 各执行一次

    with pytest.raises(LubanError) as exc_info:
        await runner.resume(flow, first.thread_id, {"approved": True})
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    assert counter["n"] == 2  # 副作用未重复执行
    runs = await _runs(db, "graph:idem")
    assert runs[0].status == RunStatus.COMPLETED  # 终态未被后续 resume 改写


async def test_parallel_resume_serialized_side_effect_once(make_runner, db: Database):
    """H12：并行 resume 同一线程串行——先到者推进至完成，后到者撞终态显式拒绝；副作用恰一次。"""
    flow, counter = _counting_flow("par")
    runner = make_runner()
    first = await runner.run(flow, {})
    assert first.status == "interrupted"

    results = await asyncio.gather(
        runner.resume(flow, first.thread_id, {"approved": True}),
        runner.resume(flow, first.thread_id, {"approved": True}),
        return_exceptions=True,
    )
    completed = [r for r in results if isinstance(r, object) and not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, LubanError)]
    assert len(completed) == 1 and completed[0].status == "completed"
    assert len(errors) == 1 and errors[0].code == ErrorCode.VALIDATION_ERROR
    assert counter["n"] == 2  # 两节点各恰执行一次，不因并发 resume 重复
    runs = await _runs(db, "graph:par")
    assert runs[0].status == RunStatus.COMPLETED


async def test_resume_twice_across_runner_instances(make_runner, db: Database):
    """H12：两次中断跨 runner 实例（快照重开）逐段恢复——第二次恢复后再次 resume 显式拒绝。"""
    counter = {"n": 0}

    def counting(state):
        counter["n"] += 1
        return {"n": counter["n"]}

    flow = (
        GraphBuilder("twice")
        .step("a", counting)
        .human("first", prompt="第一次确认")
        .human("second", prompt="第二次确认")
        .step("b", counting)
        .entry("a")
        .edge("a", "first")
        .edge("first", "second")
        .edge("second", "b")
        .build()
    )
    runner_a = make_runner()
    first = await runner_a.run(flow, {})
    assert first.status == "interrupted"

    second = await runner_a.resume(flow, first.thread_id, {"approved": True})
    assert second.status == "interrupted"  # 第二个人工节点

    # 快照重开：换新 runner 实例（同一 DB checkpoint）完成第二段恢复
    runner_b = make_runner()
    third = await runner_b.resume(flow, first.thread_id, {"approved": True})
    assert third.status == "completed"
    assert counter["n"] == 2

    with pytest.raises(LubanError) as exc_info:
        await runner_b.resume(flow, first.thread_id, {"approved": True})
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    assert counter["n"] == 2
