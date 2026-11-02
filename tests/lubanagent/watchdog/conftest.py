"""watchdog 测试基建：种 RUNNING Run + 旧时间戳事件 + 假时钟，全确定性。"""

from datetime import UTC, datetime, timedelta

import pytest

from lubanagent.config import Settings
from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    Run,
    RunStatus,
    TraceEventType,
)
from lubanagent.core.trace import Trace, TraceEvent
from lubanagent.persistence import (
    AgentRepository,
    Base,
    Database,
    RunRepository,
    TraceRepository,
)


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        auth_enabled=False,
        observability_backend="none",
        env="dev",
        watchdog_enabled=True,
        watchdog_interval_seconds=60.0,
        watchdog_stale_after_seconds=300.0,
        watchdog_max_run_seconds=3600.0,
    )


@pytest.fixture
def registry():
    from lubanagent.extension import Registry

    return Registry()


def make_clock(now: datetime):
    return lambda: now


async def seed_agent(db: Database, key: str = "wd-agent") -> None:
    async with db.session() as session:
        await AgentRepository(session).create_agent(AgentDefinition(key=key, name=key))


async def seed_run(
    db: Database,
    run_id: str,
    *,
    status: RunStatus = RunStatus.RUNNING,
    started_at: datetime,
    last_event_at: datetime | None = None,
    agent_key: str = "wd-agent",
) -> None:
    """种一个 Run（可带末条 trace 事件，时间戳如实保留）。"""
    async with db.session() as session:
        await RunRepository(session).create_run(
            Run(
                id=run_id,
                agent_key=agent_key,
                agent_version=1,
                status=status,
                brain_profile=BrainProfile.SMART,
                created_at=started_at,
                started_at=started_at,
            )
        )
        trace_repo = TraceRepository(session)
        await trace_repo.create_trace(
            Trace(
                id=f"trace-{run_id}",
                run_id=run_id,
                status=status,
                started_at=started_at,
            )
        )
        if last_event_at is not None:
            await trace_repo.add_event(
                TraceEvent(
                    id=f"evt-{run_id}",
                    run_id=run_id,
                    type=TraceEventType.MODEL_CALL_COMPLETED,
                    payload={},
                    created_at=last_event_at,
                )
            )


NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC)


def minutes(n: int) -> datetime:
    return NOW - timedelta(minutes=n)
