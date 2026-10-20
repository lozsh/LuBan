"""Noop 后端：零依赖关闭外部外发（OBSERVABILITY_BACKEND=none），内部 DB 仍 always on。"""

from typing import Any

from lubanagent.core import Run, RunStatus, TraceEvent
from lubanagent.extension.protocols import TraceHandle


class NoneHandle:
    """空 TraceHandle 实现。"""

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id


class NoopBackend:
    """三方法全部无操作的 ObservabilityBackend 实现。"""

    @property
    def name(self) -> str:
        return "noop"

    def start_trace(self, run: Run) -> TraceHandle:
        return NoneHandle(run_id=run.id)

    def emit(self, handle: TraceHandle, event: TraceEvent) -> None:
        return None

    def replay(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """noop 无外发（也从不产生失败留痕），回放为空操作。"""
        return None

    def end_trace(
        self, handle: TraceHandle, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        return None
