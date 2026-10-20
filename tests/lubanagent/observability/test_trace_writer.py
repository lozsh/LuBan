"""TraceWriter：五表双写一致 + Trace 七问可答（内部 DB 是 source of truth）。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    EndReason,
    ErrorCode,
    LubanError,
    Run,
    RunStatus,
    TokenUsage,
    TraceEvent,
    TraceEventType,
)
from lubanagent.observability import TraceWriter
from lubanagent.persistence import Database, RunRepository, TraceRepository
from lubanagent.persistence.models import (
    CostRecordRow,
    RunRow,
    TokenUsageRow,
    TraceEventRow,
    TraceRow,
)
from lubanagent.persistence.repositories import AgentRepository

T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def make_run(run_id: str = "run-1") -> Run:
    return Run(
        id=run_id,
        agent_key="demo",
        agent_version=2,
        session_id="sess-1",
        status=RunStatus.RUNNING,
        input={"question": "怎么退款"},
        brain_profile=BrainProfile.SMART,
        model_key="deepseek-chat",
        created_at=T0,
        started_at=T0,
    )


def make_event(
    event_id: str,
    event_type: TraceEventType,
    *,
    run_id: str = "run-1",
    step_index: int | None = None,
    payload: dict | None = None,
) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        run_id=run_id,
        type=event_type,
        step_index=step_index,
        payload=payload or {},
        created_at=T0,
    )


async def seed_agent(db: Database) -> None:
    async with db.session() as session:
        await AgentRepository(session).create_agent(
            AgentDefinition(key="demo", name="demo", version=2)
        )


async def test_full_flow_five_tables_consistent(db: Database) -> None:
    """start→emit→record_model_usage→record_cost→finish 后五表内容与输入一致。"""
    await seed_agent(db)
    writer = TraceWriter(db)
    run = make_run()

    trace = await writer.start_run(run)
    assert trace.run_id == "run-1"

    await writer.emit(
        make_event(
            "e-model-start",
            TraceEventType.MODEL_CALL_STARTED,
            step_index=0,
            payload={"model_key": "deepseek-chat", "input": "prompt: 怎么退款"},
        )
    )
    await writer.emit(
        make_event(
            "e-model-done",
            TraceEventType.MODEL_CALL_COMPLETED,
            step_index=0,
            payload={"input_tokens": 100, "output_tokens": 50, "output": "answer"},
        )
    )
    await writer.record_model_usage(
        "run-1",
        step_index=0,
        model_key="deepseek-chat",
        input_tokens=100,
        output_tokens=50,
    )
    await writer.record_cost("run-1", model_key="deepseek-chat", cost=0.0042)

    finished = run.model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "output": "answer",
            "end_reason": EndReason.FINAL_ANSWER,
            "token_usage": TokenUsage(input_tokens=100, output_tokens=50, model_calls=1),
            "cost": 0.0042,
            "ended_at": datetime(2026, 7, 23, 12, 1, 0, tzinfo=UTC),
        }
    )
    await writer.finish_run(finished)

    async with db.session() as session:
        run_row = await session.get(RunRow, "run-1")
        assert run_row.status == "completed"
        assert run_row.agent_version_id is not None
        assert run_row.model_key == "deepseek-chat"
        assert run_row.end_reason == "final_answer"
        assert run_row.cost == pytest.approx(0.0042)
        assert run_row.token_usage == {
            "input_tokens": 100,
            "output_tokens": 50,
            "model_calls": 1,
        }

        trace_row = await session.scalar(select(TraceRow).where(TraceRow.run_id == "run-1"))
        assert trace_row.status == "completed"
        assert trace_row.ended_at is not None

        events = (
            await session.scalars(
                select(TraceEventRow)
                .where(TraceEventRow.run_id == "run-1")
                .order_by(TraceEventRow.id)
            )
        ).all()
        types = {e.type for e in events}
        assert types == {
            "run_started",
            "model_call_started",
            "model_call_completed",
            "run_completed",
        }
        assert all(e.trace_id == trace_row.id for e in events)

        usage_row = await session.scalar(
            select(TokenUsageRow).where(TokenUsageRow.run_id == "run-1")
        )
        assert (usage_row.model_key, usage_row.input_tokens, usage_row.output_tokens) == (
            "deepseek-chat",
            100,
            50,
        )
        assert usage_row.step_index == 0

        cost_row = await session.scalar(
            select(CostRecordRow).where(CostRecordRow.run_id == "run-1")
        )
        assert cost_row.cost == pytest.approx(0.0042)
        assert cost_row.currency == "CNY"


async def test_seven_questions_answerable_from_db(db: Database) -> None:
    """七问：版本/模型/Prompt 痕迹/工具调用/Token/费用/结束原因，全部由内部 DB 回答。"""
    await seed_agent(db)
    writer = TraceWriter(db)
    run = make_run()
    await writer.start_run(run)

    await writer.emit(
        make_event(
            "e1",
            TraceEventType.MODEL_CALL_STARTED,
            step_index=0,
            payload={"model_key": "deepseek-chat", "input": "system: 你是客服\nuser: 怎么退款"},
        )
    )
    await writer.emit(
        make_event(
            "e2",
            TraceEventType.TOOL_CALL_STARTED,
            step_index=1,
            payload={"tool_key": "refund.query", "input": {"order_id": "A1"}},
        )
    )
    await writer.emit(
        make_event(
            "e3",
            TraceEventType.TOOL_CALL_COMPLETED,
            step_index=1,
            payload={"tool_key": "refund.query", "output": {"status": "refundable"}},
        )
    )
    await writer.emit(
        make_event(
            "e4",
            TraceEventType.MODEL_CALL_COMPLETED,
            step_index=2,
            payload={"input_tokens": 120, "output_tokens": 30, "output": "可以退款"},
        )
    )
    await writer.record_model_usage(
        "run-1", step_index=2, model_key="deepseek-chat", input_tokens=120, output_tokens=30
    )
    await writer.record_cost("run-1", model_key="deepseek-chat", cost=0.003)

    finished = run.model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "output": "可以退款",
            "end_reason": EndReason.FINAL_ANSWER,
            "token_usage": TokenUsage(input_tokens=120, output_tokens=30, model_calls=1),
            "cost": 0.003,
            "ended_at": datetime(2026, 7, 23, 12, 2, 0, tzinfo=UTC),
        }
    )
    await writer.finish_run(finished)

    async with db.session() as session:
        loaded = await RunRepository(session).get_run("run-1")
        events = await TraceRepository(session).list_events("run-1")

        # 1. 版本：哪个 Agent 哪个版本
        assert (loaded.agent_key, loaded.agent_version) == ("demo", 2)
        # 2. 模型：用了哪个模型
        assert loaded.model_key == "deepseek-chat"
        # 3. Prompt 痕迹：事件 payload 可回放完整输入
        model_started = next(
            e for e in events if e.type is TraceEventType.MODEL_CALL_STARTED
        )
        assert "怎么退款" in model_started.payload["input"]
        # 4. 工具调用：调了什么工具、入参出参
        tool_started = next(
            e for e in events if e.type is TraceEventType.TOOL_CALL_STARTED
        )
        tool_done = next(
            e for e in events if e.type is TraceEventType.TOOL_CALL_COMPLETED
        )
        assert tool_started.payload["tool_key"] == "refund.query"
        assert tool_started.payload["input"] == {"order_id": "A1"}
        assert tool_done.payload["output"] == {"status": "refundable"}
        # 5. Token：明细与汇总
        usage_total = await session.scalar(
            select(func.sum(TokenUsageRow.input_tokens + TokenUsageRow.output_tokens)).where(
                TokenUsageRow.run_id == "run-1"
            )
        )
        assert usage_total == 150
        assert loaded.token_usage.total == 150
        # 6. 费用：明细与汇总
        cost_total = await session.scalar(
            select(func.sum(CostRecordRow.cost)).where(CostRecordRow.run_id == "run-1")
        )
        assert cost_total == pytest.approx(0.003)
        assert loaded.cost == pytest.approx(0.003)
        # 7. 结束原因：runs 字段 + 终态事件
        assert loaded.end_reason is EndReason.FINAL_ANSWER
        completed = next(e for e in events if e.type is TraceEventType.RUN_COMPLETED)
        assert completed.payload["end_reason"] == "final_answer"


async def test_emit_persists_redacted_audit_copy(db: Database) -> None:
    """入库 payload 为脱敏审计副本：秘密键隐藏、业务字段保留、原事件不被改写。"""
    await seed_agent(db)
    writer = TraceWriter(db)
    await writer.start_run(make_run())
    event = make_event(
        "e-redact",
        TraceEventType.TOOL_CALL_STARTED,
        payload={
            "tool": "refund",
            "args": {"order_id": "A-1", "api_key": "sk-synthetic-secret"},
            "note": "正常业务字段",
        },
    )
    await writer.emit(event)
    async with db.session() as session:
        events = await TraceRepository(session).list_events("run-1")
    stored = next(e for e in events if e.id == "e-redact")
    assert stored.payload["args"]["api_key"] == "[REDACTED]"
    assert stored.payload["args"]["order_id"] == "A-1"
    assert stored.payload["note"] == "正常业务字段"
    # 调用方原事件不被修改
    assert event.payload["args"]["api_key"] == "sk-synthetic-secret"


async def test_emit_without_start_raises(db: Database) -> None:
    writer = TraceWriter(db)
    with pytest.raises(LubanError) as exc_info:
        await writer.emit(make_event("e1", TraceEventType.STEP_STARTED, run_id="ghost"))
    assert exc_info.value.code == ErrorCode.RUN_NOT_FOUND


async def test_finish_non_terminal_raises(db: Database) -> None:
    await seed_agent(db)
    writer = TraceWriter(db)
    run = make_run()
    await writer.start_run(run)
    with pytest.raises(LubanError) as exc_info:
        await writer.finish_run(run)  # 仍是 RUNNING
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_finish_failed_run_writes_failed_events(db: Database) -> None:
    await seed_agent(db)
    writer = TraceWriter(db)
    run = make_run()
    await writer.start_run(run)
    failed = run.model_copy(
        update={
            "status": RunStatus.FAILED,
            "end_reason": EndReason.ERROR,
            "error": {"code": "INTERNAL_ERROR", "message": "boom"},
            "ended_at": datetime(2026, 7, 23, 12, 3, 0, tzinfo=UTC),
        }
    )
    await writer.finish_run(failed)
    async with db.session() as session:
        events = await TraceRepository(session).list_events("run-1")
        failed_event = next(e for e in events if e.type is TraceEventType.RUN_FAILED)
        assert failed_event.payload["end_reason"] == "error"
        trace = await TraceRepository(session).get_trace_by_run("run-1")
        assert trace.status is RunStatus.FAILED
