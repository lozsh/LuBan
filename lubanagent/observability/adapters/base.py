"""适配器基座：ObservabilityBackend 唯一定义在 extension.protocols，此处仅 re-export；

SafeBackend 提供失败隔离（架构 §6.4）：外发异常不阻断 Run。
失败留痕 export_failures 是完整消费闭环（风险 R3/R4 修复）：
- 同步上下文（无运行 loop）失败先入内存缓冲，下次有 loop 时（emit/drain）落 sink；
- drain 收尾回放待重试记录：成功删除、失败计一次尝试、达上限放弃（保留行供运维查询）。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from lubanagent.core import Run, RunStatus, TraceEvent
from lubanagent.core.redact import redact_value
from lubanagent.extension.protocols import (  # re-export
    ObservabilityBackend,
    TraceHandle,
)
from lubanagent.persistence import Database, ExportFailureRepository
from lubanagent.persistence.repositories.export_failure import DEFAULT_MAX_ATTEMPTS

logger = logging.getLogger(__name__)


def _redact_event(event: TraceEvent) -> TraceEvent:
    """构造 payload 脱敏后的事件副本，不改调用方原事件。"""
    return TraceEvent(
        id=event.id,
        run_id=event.run_id,
        type=event.type,
        step_index=event.step_index,
        payload=redact_value(event.payload),
        created_at=event.created_at,
    )

FailureSink = Callable[[str, str, str, dict[str, Any], str], Awaitable[None]]
"""失败留痕回调：(backend, run_id, event_type, payload, error_message)。"""

INLINE_BUFFER_LIMIT = 1000
"""同步上下文失败缓冲上限：超出丢最旧并记日志（正常路径 emit/drain 会及时落库）。"""


def db_failure_sink(db: Database) -> FailureSink:
    """写 export_failures 表的标准 sink。"""

    async def _sink(
        backend: str, run_id: str, event_type: str, payload: dict[str, Any], error: str
    ) -> None:
        async with db.session() as session:
            await ExportFailureRepository(session).record(
                backend, run_id, event_type, payload, error
            )

    return _sink


class SafeBackend:
    """失败隔离包装：emit fire-and-forget（Semaphore 限流），异常只记日志 + 写 sink。

    观测外发唯一出口（resolve_backend 统一包装）：交给 inner 与 sink 的
    event payload / summary 均为脱敏副本，秘密不外发第三方观测平台。
    """

    def __init__(
        self,
        inner: ObservabilityBackend,
        *,
        sink: FailureSink | None = None,
        max_concurrency: int = 8,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_batch_size: int = 100,
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._sem = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()
        self._inflight = 0  # 持有信号量的任务数（限流可观测）
        self._retry_db: Database | None = None
        self._max_attempts = max_attempts  # 重试上限：达限放弃（行保留供运维查询）
        self._retry_batch_size = retry_batch_size
        self._inline_failures: list[tuple[str, str, dict[str, Any], str]] = []
        self._replay_unsupported_logged = False

    @property
    def name(self) -> str:
        return f"{self._inner.name}"

    def set_sink(self, sink: FailureSink | None) -> None:
        """装配层注入失败上报 sink（公开口，替代私有属性写入）。"""
        self._sink = sink

    def set_retry_db(self, db: Database) -> None:
        """装配层注入重试数据源：drain 时回放 export_failures 待重试记录。"""
        self._retry_db = db

    def start_trace(self, run: Run) -> TraceHandle:
        """同步契约直接委托；建句柄失败兜底：异常 → 返回 NoneHandle 并记日志。"""
        from lubanagent.observability.adapters.noop import NoneHandle

        try:
            return self._inner.start_trace(run)
        except Exception:
            logger.warning("观测后端 %s start_trace 失败，降级为空句柄", self.name, exc_info=True)
            return NoneHandle()

    def emit(self, handle: TraceHandle, event: TraceEvent) -> None:
        """fire-and-forget：有运行中的 loop 则建 task 委托；同步上下文直接委托。

        内层异常一律捕获 → 记日志 → 写 sink（同步上下文无法 await sink 时先入缓冲）。
        外发前对 payload 脱敏（构造副本，不改调用方事件）。
        """
        event = _redact_event(event)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._emit_inline(handle, event)
            return
        self._flush_inline_failures()  # 顺带把同步上下文攒下的失败落库
        task = loop.create_task(self._emit_guarded(handle, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def end_trace(
        self, handle: TraceHandle, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        """同步委托（summary 脱敏副本）；异常 → sink（有运行中的 loop 时调度）+ 日志。"""
        safe_summary = redact_value(summary)
        try:
            self._inner.end_trace(handle, status, safe_summary)
        except Exception as exc:
            logger.warning("观测后端 %s end_trace 失败", self.name, exc_info=True)
            self._report(
                run_id=getattr(handle, "run_id", "") or "",
                event_type="end_trace",
                payload={"status": status.value, "summary": safe_summary or {}},
                error=str(exc),
            )

    async def drain(self, timeout: float = 5.0) -> None:  # noqa: ASYNC109  # 契约钉死签名
        """等飞行中 emit 收尾（Run 结束/进程退出前调用）。

        收尾顺带做两件事：同步上下文攒下的失败先落 sink；再回放待重试的外发失败。
        """
        self._flush_inline_failures()
        while self._tasks:
            tasks = list(self._tasks)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout
                )
            except TimeoutError:
                logger.warning("观测后端 %s drain 超时，剩余 %d 个任务", self.name, len(tasks))
                return
        await self._sweep_failures()

    # ---- 内部 ----
    def _emit_inline(self, handle: TraceHandle, event: TraceEvent) -> None:
        try:
            self._inner.emit(handle, event)
        except Exception as exc:
            logger.warning(
                "观测后端 %s emit 失败（同步上下文，失败先入缓冲待落库）", self.name, exc_info=True
            )
            self._buffer_failure(
                run_id=event.run_id,
                event_type=event.type.value,
                payload=event.payload,
                error=str(exc),
            )

    async def _emit_guarded(self, handle: TraceHandle, event: TraceEvent) -> None:
        async with self._sem:
            self._inflight += 1
            try:
                await asyncio.sleep(0)  # 让出调度，限流上限可被观测
                self._inner.emit(handle, event)
            except Exception as exc:
                logger.warning("观测后端 %s emit 失败", self.name, exc_info=True)
                await self._report_async(
                    run_id=event.run_id,
                    event_type=event.type.value,
                    payload=event.payload,
                    error=str(exc),
                )
            finally:
                self._inflight -= 1

    def _report(
        self, *, run_id: str, event_type: str, payload: dict[str, Any], error: str
    ) -> None:
        """同步上下文上报：有运行中的 loop 则调度 sink task，否则入缓冲待落库。"""
        if self._sink is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._buffer_failure(
                run_id=run_id, event_type=event_type, payload=payload, error=error
            )
            return
        self._schedule_report(run_id=run_id, event_type=event_type, payload=payload, error=error)

    def _schedule_report(
        self, *, run_id: str, event_type: str, payload: dict[str, Any], error: str
    ) -> None:
        """在运行中的 loop 上调度 sink task（进 self._tasks，drain 可等）。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self._report_async(run_id=run_id, event_type=event_type, payload=payload, error=error)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _report_async(
        self, *, run_id: str, event_type: str, payload: dict[str, Any], error: str
    ) -> None:
        if self._sink is None:
            return
        try:
            await self._sink(self.name, run_id, event_type, payload, error)
        except Exception:
            logger.warning("观测失败留痕 sink 自身异常: backend=%s", self.name, exc_info=True)

    def _buffer_failure(
        self, *, run_id: str, event_type: str, payload: dict[str, Any], error: str
    ) -> None:
        """同步上下文（无运行 loop）失败入内存缓冲，下次有 loop 时（emit/drain）落 sink。"""
        if self._sink is None:  # 无 sink 时缓冲无意义，只留结构化日志
            logger.warning(
                "外发失败留痕（无 sink 仅记日志）: backend=%s run=%s event=%s error=%s",
                self.name, run_id, event_type, error,
            )
            return
        self._inline_failures.append((run_id, event_type, payload, error))
        if len(self._inline_failures) > INLINE_BUFFER_LIMIT:
            dropped = self._inline_failures.pop(0)
            logger.warning(
                "同步上下文失败缓冲超上限丢最旧: backend=%s run=%s event=%s error=%s",
                self.name, dropped[0], dropped[1], dropped[3],
            )
        logger.warning(
            "外发失败留痕（同步上下文缓冲，待异步落库）: backend=%s run=%s event=%s error=%s",
            self.name, run_id, event_type, error,
        )

    def _flush_inline_failures(self) -> None:
        """把缓冲的同步上下文失败调度到 sink（须在有运行 loop 时调用）。"""
        if not self._inline_failures:
            return
        buffered, self._inline_failures = self._inline_failures, []
        for run_id, event_type, payload, error in buffered:
            self._schedule_report(
                run_id=run_id, event_type=event_type, payload=payload, error=error
            )

    async def _sweep_failures(self) -> None:
        """drain 收尾回放待重试留痕：成功删记录、失败计一次尝试、达上限放弃。"""
        if self._retry_db is None:
            return
        replay = getattr(self._inner, "replay", None)
        if replay is None:
            if not self._replay_unsupported_logged:  # 只告警一次，避免每次 drain 刷屏
                logger.warning(
                    "观测后端 %s 未实现 replay(run_id, event_type, payload)，"
                    "export_failures 留痕不自动重试（可经仓储查询手动回放）",
                    self.name,
                )
                self._replay_unsupported_logged = True
            return
        try:
            async with self._retry_db.session() as session:
                pending = await ExportFailureRepository(session).list_pending(
                    backend=self.name,
                    limit=self._retry_batch_size,
                    max_attempts=self._max_attempts,
                )
            if not pending:
                return
            succeeded: list[int] = []
            failed: list[int] = []
            for row in pending:
                try:
                    replay(row["run_id"], row["event_type"], row["payload"])
                except Exception:
                    failed.append(row["id"])
                    logger.warning(
                        "export_failures 重试失败: id=%s backend=%s run=%s event=%s 第 %d 次尝试",
                        row["id"], self.name, row["run_id"], row["event_type"],
                        row["retry_count"] + 1,
                        exc_info=True,
                    )
                else:
                    succeeded.append(row["id"])
                    logger.info(
                        "export_failures 重试成功回放: id=%s backend=%s run=%s event=%s",
                        row["id"], self.name, row["run_id"], row["event_type"],
                    )
            async with self._retry_db.session() as session:
                repo = ExportFailureRepository(session)
                await repo.delete(succeeded)
                await repo.mark_retry_failed(failed)
            for row in pending:
                if row["id"] in failed and row["retry_count"] + 1 >= self._max_attempts:
                    logger.warning(
                        "export_failures 重试达上限放弃（行保留供运维查询）: "
                        "id=%s backend=%s run=%s event=%s 共尝试 %d 次",
                        row["id"], self.name, row["run_id"], row["event_type"],
                        row["retry_count"] + 1,
                    )
            logger.info(
                "export_failures 重试一轮: backend=%s 待重试=%d 成功=%d 失败=%d",
                self.name, len(pending), len(succeeded), len(failed),
            )
        except Exception:
            # 重试消费自身异常不阻断 drain / Run 收尾
            logger.warning("export_failures 重试消费异常（不阻断 drain）", exc_info=True)
