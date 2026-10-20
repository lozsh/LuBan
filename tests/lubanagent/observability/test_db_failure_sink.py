"""db_failure_sink：SafeBackend 失败留痕落 export_failures 表（端到端）。"""

from datetime import UTC, datetime
from typing import Any

from lubanagent.core import BrainProfile, Run, RunStatus, TraceEvent, TraceEventType
from lubanagent.observability import SafeBackend, db_failure_sink
from lubanagent.persistence import Database, ExportFailureRepository

T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


class ExplodingBackend:
    @property
    def name(self) -> str:
        return "langfuse"

    def start_trace(self, run: Run) -> object:
        return object()

    def emit(self, handle: Any, event: TraceEvent) -> None:
        raise ConnectionError("langfuse unreachable")

    def end_trace(
        self, handle: Any, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        return None


async def test_emit_failure_lands_in_export_failures(db: Database) -> None:
    backend = SafeBackend(ExplodingBackend(), sink=db_failure_sink(db))
    run = Run(
        id="run-9",
        agent_key="demo",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        created_at=T0,
    )
    handle = backend.start_trace(run)
    event = TraceEvent(
        id="e1",
        run_id="run-9",
        type=TraceEventType.MODEL_CALL_COMPLETED,
        payload={"input_tokens": 1},
        created_at=T0,
    )
    backend.emit(handle, event)  # 不阻断
    await backend.drain()

    async with db.session() as session:
        pending = await ExportFailureRepository(session).list_pending()
    assert len(pending) == 1
    row = pending[0]
    assert row["backend"] == "langfuse"
    assert row["run_id"] == "run-9"
    assert row["event_type"] == "model_call_completed"
    assert row["payload"] == {"input_tokens": 1}
    assert "langfuse unreachable" in row["error"]
    assert row["retried_at"] is None
    assert row["retry_count"] == 0  # 新留痕未尝试重试
