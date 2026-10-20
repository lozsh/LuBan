"""TraceRepository：Trace 头、事件追加与按序读取、收尾。"""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    ErrorCode,
    LubanError,
    Run,
    RunStatus,
    Trace,
    TraceEvent,
    TraceEventType,
)
from lubanagent.persistence.repositories import AgentRepository, RunRepository, TraceRepository


async def seed_run(session: AsyncSession, run_id: str = "run-1") -> None:
    await AgentRepository(session).create_agent(AgentDefinition(key="demo", name="demo"))
    await RunRepository(session).create_run(
        Run(
            id=run_id,
            agent_key="demo",
            agent_version=1,
            brain_profile=BrainProfile.SMART,
            created_at=datetime(2026, 7, 23, 12, 0, 0),
        )
    )


def make_trace(run_id: str = "run-1") -> Trace:
    return Trace(
        id=f"trace-{run_id}",
        run_id=run_id,
        status=RunStatus.RUNNING,
        started_at=datetime(2026, 7, 23, 12, 0, 0),
    )


def make_event(event_id: str, run_id: str = "run-1", seconds: int = 1) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        run_id=run_id,
        type=TraceEventType.STEP_COMPLETED,
        step_index=0,
        payload={"k": event_id},
        created_at=datetime(2026, 7, 23, 12, 0, seconds),
    )


async def test_create_and_get_trace_by_run(session: AsyncSession) -> None:
    await seed_run(session)
    repo = TraceRepository(session)
    await repo.create_trace(make_trace())

    loaded = await repo.get_trace_by_run("run-1")
    assert loaded is not None
    assert loaded.id == "trace-run-1"
    assert loaded.status == RunStatus.RUNNING
    assert loaded.started_at == datetime(2026, 7, 23, 12, 0, 0)
    assert loaded.ended_at is None

    assert await repo.get_trace_by_run("ghost") is None


async def test_add_and_list_events_ordered(session: AsyncSession) -> None:
    await seed_run(session)
    repo = TraceRepository(session)
    await repo.create_trace(make_trace())
    await repo.add_event(make_event("e2", seconds=2))
    await repo.add_event(make_event("e1", seconds=1))

    events = await repo.list_events("run-1")
    assert [e.id for e in events] == ["e1", "e2"]  # 按 created_at、id 升序
    assert events[0].type == TraceEventType.STEP_COMPLETED
    assert events[0].payload == {"k": "e1"}
    assert events[0].step_index == 0


async def test_add_event_without_trace_raises(session: AsyncSession) -> None:
    with pytest.raises(LubanError) as exc_info:
        await TraceRepository(session).add_event(make_event("e1", run_id="ghost"))
    assert exc_info.value.code == ErrorCode.RUN_NOT_FOUND


async def test_finish_trace(session: AsyncSession) -> None:
    await seed_run(session)
    repo = TraceRepository(session)
    await repo.create_trace(make_trace())
    ended = datetime(2026, 7, 23, 12, 1, 0)
    await repo.finish_trace("run-1", RunStatus.COMPLETED, ended)

    loaded = await repo.get_trace_by_run("run-1")
    assert loaded is not None
    assert loaded.status == RunStatus.COMPLETED
    assert loaded.ended_at == ended
