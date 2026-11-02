"""异步工具任务登记处与伴生工具（异步 ReAct Loop 的 start/stop/result 语义）。

模式：async 工具经 bridge 同步段走完 preflight（审批前置）后立即返回占位
（status=started + 确定性 task_id），后台协程跑 executor.launch；模型用
task_result(task_id) 取结果、task_stop(task_id) 终止。per-run 生命周期，
不持久化——进程重启后任务丢失是**如实降级**（task_result 返回 not_found，
不假装可恢复），僵尸 Run 由外部 Watchdog 兜底。

task_id 确定性生成（task-1/task-2 自增）是可测性前提：FakeLLM 剧本须在
Run 前静态写死 task_result 的参数。
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from lubanagent.core import RiskLevel, RunContext, ToolDescriptor, TraceEvent, TraceEventType
from lubanagent.extension import RegisteredTool
from lubanagent.tool.executor import ToolExecutor, ToolResult

TaskStatus = Literal["running", "completed", "failed", "cancelled"]


@dataclass
class AsyncTaskRecord:
    """一次异步工具任务；result 收尾后填，collected 标记模型是否经 task_result 收走。"""

    task_id: str
    tool_key: str
    started_at: datetime
    step_index: int
    status: TaskStatus = "running"
    result: ToolResult | None = None
    collected: bool = False
    asyncio_task: asyncio.Task | None = None
    cancel_reason: str = "stopped"  # 被取消时监督协程发 CANCELLED 事件用的 reason


def _new_task_event(
    run_id: str,
    event_type: TraceEventType,
    payload: dict[str, Any],
    *,
    step_index: int,
) -> TraceEvent:
    return TraceEvent(
        id=f"evt-{uuid.uuid4().hex}",
        run_id=run_id,
        type=event_type,
        step_index=step_index,
        payload=dict(payload),
        created_at=datetime.now(UTC),
    )


class AsyncToolRunner:
    """per-run 任务登记处（单 run 生命周期，不持久化）；监督任务收敛终态与事件。

    后台协程只发 TOOL_TASK_* 事件、只读启动时快照——绝不写 step_state
    （单写者原则：主循环协程独占）；completed/failed 事件的 step_index 固定为
    启动步（历史定位语义）。
    """

    def __init__(self, *, max_concurrent: int = 8) -> None:
        self._max_concurrent = max_concurrent
        self._tasks: dict[str, AsyncTaskRecord] = {}
        self._seq = 0

    def start(
        self,
        *,
        run_id: str,
        registered: RegisteredTool,
        validated_args: dict[str, Any],
        executor: ToolExecutor,
        ctx: RunContext,
        emit: Any,
        step_index: int,
        token: Any = None,
    ) -> AsyncTaskRecord | None:
        """spawn 后台监督任务；并发满返回 None（调用方回喂错误，不阻塞）。"""
        running = sum(1 for r in self._tasks.values() if r.status == "running")
        if running >= self._max_concurrent:
            return None
        self._seq += 1
        record = AsyncTaskRecord(
            task_id=f"task-{self._seq}",
            tool_key=registered.descriptor.key,
            started_at=datetime.now(UTC),
            step_index=step_index,
        )
        self._tasks[record.task_id] = record
        record.asyncio_task = asyncio.create_task(
            self._supervise(
                record,
                run_id=run_id,
                registered=registered,
                validated_args=validated_args,
                executor=executor,
                ctx=ctx,
                emit=emit,
                token=token,
            )
        )
        return record

    def get(self, task_id: str) -> AsyncTaskRecord | None:
        return self._tasks.get(task_id)

    def stop(self, task_id: str) -> bool:
        """终止任务（模型主动 task_stop）；返回是否终止了一个 running 任务。"""
        record = self._tasks.get(task_id)
        if record is None or record.status != "running":
            return False
        if record.asyncio_task is not None:
            record.asyncio_task.cancel()
        return True

    async def cancel_all(self, *, reason: str, emit: Any) -> list[str]:
        """终态清理：取消未收任务、等监督任务收敛（含发 TOOL_TASK_CANCELLED）。

        返回 abandoned（模型从未经 task_result 收走结果）的 task_id 列表。
        """
        for record in self._tasks.values():
            if record.status == "running" and record.asyncio_task is not None:
                record.cancel_reason = reason
                record.asyncio_task.cancel()
        pending = [
            r.asyncio_task
            for r in self._tasks.values()
            if r.asyncio_task is not None and not r.asyncio_task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return sorted(r.task_id for r in self._tasks.values() if not r.collected)

    async def _supervise(
        self,
        record: AsyncTaskRecord,
        *,
        run_id: str,
        registered: RegisteredTool,
        validated_args: dict[str, Any],
        executor: ToolExecutor,
        ctx: RunContext,
        emit: Any,
        token: Any,
    ) -> None:
        """监督协程：执行工具、监听 Run 取消、发终态事件。吞 CancelledError
        （stop/终态清理把它取消是**领域语义**而非异常，转成 cancelled 记录）。"""
        waiter = token.subscribe_async() if token is not None else None
        exec_task = asyncio.create_task(executor.launch(registered, validated_args, ctx=ctx))
        waiter_task = asyncio.create_task(waiter.wait()) if waiter is not None else None
        try:
            waiters: set[asyncio.Future] = {exec_task}
            if waiter_task is not None:
                waiters.add(waiter_task)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if waiter_task is not None and not waiter_task.done():
                waiter_task.cancel()
                await asyncio.gather(waiter_task, return_exceptions=True)
            if waiter_task is not None and waiter_task in done and not exec_task.done():
                exec_task.cancel()
                await asyncio.gather(exec_task, return_exceptions=True)
                record.status = "cancelled"
                await emit(
                    _new_task_event(
                        run_id,
                        TraceEventType.TOOL_TASK_CANCELLED,
                        {
                            "tool": record.tool_key,
                            "task_id": record.task_id,
                            "reason": "run_cancelled",
                        },
                        step_index=record.step_index,
                    )
                )
                return
            record.result = exec_task.result()
            record.status = "completed" if record.result.success else "failed"
            if record.result.success:
                await emit(
                    _new_task_event(
                        run_id,
                        TraceEventType.TOOL_TASK_COMPLETED,
                        {
                            "tool": record.tool_key,
                            "task_id": record.task_id,
                            "result": record.result.value,
                            "duration_ms": record.result.duration_ms,
                        },
                        step_index=record.step_index,
                    )
                )
            else:
                await emit(
                    _new_task_event(
                        run_id,
                        TraceEventType.TOOL_TASK_FAILED,
                        {
                            "tool": record.tool_key,
                            "task_id": record.task_id,
                            "error": record.result.error,
                        },
                        step_index=record.step_index,
                    )
                )
        except asyncio.CancelledError:
            if waiter_task is not None and not waiter_task.done():
                waiter_task.cancel()
                await asyncio.gather(waiter_task, return_exceptions=True)
            exec_task.cancel()
            await asyncio.gather(exec_task, return_exceptions=True)
            record.status = "cancelled"
            await emit(
                _new_task_event(
                    run_id,
                    TraceEventType.TOOL_TASK_CANCELLED,
                    {
                        "tool": record.tool_key,
                        "task_id": record.task_id,
                        "reason": record.cancel_reason,
                    },
                    step_index=record.step_index,
                )
            )

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        """task_result 伴生工具的读回视图；首次读到终态即标记 collected。"""
        record = self._tasks.get(task_id)
        if record is None:
            return None
        elapsed_ms = (datetime.now(UTC) - record.started_at).total_seconds() * 1000.0
        if record.status == "running":
            return {"status": "running", "task_id": task_id, "elapsed_ms": round(elapsed_ms, 1)}
        record.collected = True
        view: dict[str, Any] = {"status": record.status, "task_id": task_id}
        if record.result is not None and record.result.success:
            view["result"] = record.result.value
        if record.result is not None and not record.result.success:
            view["error"] = record.result.error
        return view


def make_task_result_tool(runner: AsyncToolRunner) -> RegisteredTool:
    """task_result(task_id)：pending 返回运行态（模型可决定等待/放弃），终态返回结果。"""

    async def handler(task_id: str) -> dict[str, Any]:
        view = runner.snapshot(task_id)
        if view is None:
            # 进程重启后 registry 为空：如实降级（不假装任务还在）
            return {
                "status": "not_found",
                "task_id": task_id,
                "hint": "任务不存在（可能因进程重启丢失）",
            }
        return view

    descriptor = ToolDescriptor(
        key="task_result",
        name="task_result",
        description="按 task_id 取回后台工具任务的结果（running 时返回运行态，可稍后再取）",
        args_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "如 task-1"}},
            "required": ["task_id"],
        },
        risk=RiskLevel.LOW,
    )
    return RegisteredTool(descriptor=descriptor, handler=handler)


def make_task_stop_tool(runner: AsyncToolRunner) -> RegisteredTool:
    """task_stop(task_id)：终止还在跑的后台任务；已结束返回 stopped=false。"""

    async def handler(task_id: str) -> dict[str, Any]:
        record = runner.get(task_id)
        if record is None:
            return {"task_id": task_id, "stopped": False, "reason": "not_found"}
        stopped = runner.stop(task_id)
        return {"task_id": task_id, "stopped": stopped}

    descriptor = ToolDescriptor(
        key="task_stop",
        name="task_stop",
        description="终止一个还在执行的后台工具任务",
        args_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "如 task-1"}},
            "required": ["task_id"],
        },
        risk=RiskLevel.LOW,
    )
    return RegisteredTool(descriptor=descriptor, handler=handler)


__all__ = [
    "AsyncTaskRecord",
    "AsyncToolRunner",
    "TaskStatus",
    "make_task_result_tool",
    "make_task_stop_tool",
]
