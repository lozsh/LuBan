"""AsyncToolRunner 与伴生工具单测：确定性 task_id / 终态收敛 / stop / 并发上限。"""

import asyncio

from lubanagent.core import RiskLevel, ToolDescriptor
from lubanagent.extension import RegisteredTool
from lubanagent.runtime.async_tools import (
    AsyncToolRunner,
    make_task_result_tool,
    make_task_stop_tool,
)
from lubanagent.tool.executor import ToolExecutor


def _registered(name: str, handler) -> RegisteredTool:
    descriptor = ToolDescriptor(
        key=name,
        name=name,
        description=name,
        args_schema={"type": "object", "properties": {}},
        risk=RiskLevel.LOW,
    )
    return RegisteredTool(descriptor=descriptor, handler=handler)


class _NullToken:
    """无取消语义的 token 替身（subscribe_async 返回永不置位的 Event）。"""

    def subscribe_async(self):
        return asyncio.Event()


def _emit_collector():
    events: list = []

    async def emit(event):
        events.append(event)

    return events, emit


def _runner_with_fast_tool(**kwargs):
    """构造 runner + 一个 sleep 0.05s 的 async 工具，返回 (runner, registered, executor)。"""
    runner = AsyncToolRunner(**kwargs)

    async def handler() -> str:
        await asyncio.sleep(0.05)
        return "done"

    registered = _registered("slow_tool", handler)
    return runner, registered, ToolExecutor()


async def test_start_assigns_sequential_task_ids():
    """task_id 确定性自增（task-1/task-2）——FakeLLM 剧本可静态写死的前提。"""
    runner, registered, executor = _runner_with_fast_tool()
    _events, emit = _emit_collector()
    ctx = None  # handler 不收 _ctx，None 即可
    r1 = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=ctx, emit=emit, step_index=0, token=_NullToken(),
    )
    r2 = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=ctx, emit=emit, step_index=1, token=_NullToken(),
    )
    assert r1 is not None and r2 is not None
    assert (r1.task_id, r2.task_id) == ("task-1", "task-2")
    await asyncio.gather(r1.asyncio_task, r2.asyncio_task)
    assert r1.status == "completed" and r1.result.value == "done"
    assert r1.step_index == 0 and r2.step_index == 1  # 事件 step_index=启动步


async def test_supervise_emits_completed_event():
    runner, registered, executor = _runner_with_fast_tool()
    events, emit = _emit_collector()
    record = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=None, emit=emit, step_index=0, token=_NullToken(),
    )
    await record.asyncio_task
    assert [e.type.value for e in events] == ["tool_task_completed"]
    assert events[0].payload["task_id"] == "task-1"
    assert events[0].payload["result"] == "done"


async def test_failed_tool_emits_failed_event():
    runner = AsyncToolRunner()

    async def boom() -> str:
        raise ValueError("x")

    registered = _registered("boom", boom)
    events, emit = _emit_collector()
    record = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=ToolExecutor(),
        ctx=None, emit=emit, step_index=2, token=_NullToken(),
    )
    await record.asyncio_task
    assert record.status == "failed"
    assert events[0].type.value == "tool_task_failed"
    assert events[0].payload["error"]["code"] == "TOOL_EXECUTION_FAILED"
    assert events[0].step_index == 2  # 启动步，不递增


async def test_task_stop_cancels_running():
    runner = AsyncToolRunner()

    async def hang() -> str:
        await asyncio.sleep(10)
        return "never"

    registered = _registered("hang", hang)
    events, emit = _emit_collector()
    record = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=ToolExecutor(),
        ctx=None, emit=emit, step_index=0, token=_NullToken(),
    )
    await asyncio.sleep(0.01)  # 等监督协程进入 wait
    assert runner.stop(record.task_id) is True
    await asyncio.wait_for(record.asyncio_task, timeout=2)
    assert record.status == "cancelled"
    assert events[-1].type.value == "tool_task_cancelled"
    assert events[-1].payload["reason"] == "stopped"
    assert runner.stop(record.task_id) is False  # 已结束不可再停


async def test_max_concurrent_rejects_spawn():
    runner, registered, executor = _runner_with_fast_tool(max_concurrent=1)
    _events, emit = _emit_collector()
    r1 = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=None, emit=emit, step_index=0, token=_NullToken(),
    )
    assert runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=None, emit=emit, step_index=1, token=_NullToken(),
    ) is None  # 并发满 → None（bridge 回喂 TOOL_TASK_LIMIT）
    await r1.asyncio_task


async def test_run_cancel_token_sets_waiter():
    """Run 取消 → token waiter 置位 → 监督协程取消 exec_task 并发 cancelled 事件。"""
    from lubanagent.runtime.cancellation import CancellationToken

    runner = AsyncToolRunner()

    async def hang() -> str:
        await asyncio.sleep(10)
        return "never"

    registered = _registered("hang", hang)
    events, emit = _emit_collector()
    token = CancellationToken()
    record = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=ToolExecutor(),
        ctx=None, emit=emit, step_index=0, token=token,
    )
    await asyncio.sleep(0.01)
    token.cancel()
    await asyncio.wait_for(record.asyncio_task, timeout=2)
    assert record.status == "cancelled"
    assert events[-1].payload["reason"] == "run_cancelled"


async def test_cancel_all_returns_abandoned():
    """终态清理：未收任务进 abandoned；snapshot 收走的不再算。"""
    runner, registered, executor = _runner_with_fast_tool()
    _events, emit = _emit_collector()
    r1 = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=None, emit=emit, step_index=0, token=_NullToken(),
    )
    r2 = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=None, emit=emit, step_index=1, token=_NullToken(),
    )
    await r1.asyncio_task
    runner.snapshot(r1.task_id)  # task_result 收走 r1
    abandoned = await runner.cancel_all(reason="run_finalized", emit=emit)
    await r2.asyncio_task
    assert abandoned == ["task-2"]


async def test_task_result_companion_views():
    """task_result 视图：running → completed（result）；终态首读即标记 collected。"""
    runner, registered, executor = _runner_with_fast_tool()
    _events, emit = _emit_collector()
    record = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=None, emit=emit, step_index=0, token=_NullToken(),
    )
    result_tool = make_task_result_tool(runner)
    stop_tool = make_task_stop_tool(runner)
    running_view = await result_tool.handler(task_id=record.task_id)
    assert running_view["status"] == "running"
    assert await result_tool.handler(task_id="task-99") == {
        "status": "not_found",
        "task_id": "task-99",
        "hint": "任务不存在（可能因进程重启丢失）",
    }
    await record.asyncio_task
    done_view = await result_tool.handler(task_id=record.task_id)
    assert done_view == {"status": "completed", "task_id": "task-1", "result": "done"}
    assert record.collected is True
    stopped = await stop_tool.handler(task_id=record.task_id)
    assert stopped == {"task_id": "task-1", "stopped": False}  # 已结束不可停


async def test_supervise_never_writes_step_state():
    """后台协程不写 step_state（单写者原则）：终态事件 step_index 固定启动步。"""
    runner, registered, executor = _runner_with_fast_tool()
    step_state = {"index": 5}
    events, emit = _emit_collector()
    record = runner.start(
        run_id="r", registered=registered, validated_args={}, executor=executor,
        ctx=None, emit=emit, step_index=step_state["index"], token=_NullToken(),
    )
    await record.asyncio_task
    assert step_state == {"index": 5}
    assert all(e.step_index == 5 for e in events)
