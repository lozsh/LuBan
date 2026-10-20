"""录制重放（H10）：Run 局部模型注入、录制结果 stub 回喂、零副作用、显式失败路径。"""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from lubanagent.core import (
    BrainProfile,
    ErrorCode,
    LubanError,
    RiskLevel,
    RunStatus,
    TraceEvent,
    TraceEventType,
)
from lubanagent.extension import Registry, tool
from lubanagent.memory import SessionMemory
from lubanagent.observability.evals import (
    REPLAY_MODEL_KEY,
    replay_run,
    script_from_events,
    tool_results_from_events,
)
from lubanagent.persistence import (
    Database,
    ModelRepository,
    RunRepository,
    TraceRepository,
)
from lubanagent.persistence.models.trace import TraceEventRow
from lubanagent.runtime import AgentRuntime, RunRequest

from .conftest import create_test_agent, seed_fake_brain

SCRIPT = [
    {"tool_calls": [{"name": "query_order", "args": {"order_id": "12345"}, "id": "c1"}]},
    {"tool_calls": [{"name": "query_order", "args": {"order_id": "67890"}, "id": "c2"}]},
    "两单都查完了",
]


def _tool_counter(registry: Registry) -> dict[str, int]:
    """注册计数版 query_order（真实业务 handler 模拟），返回计数器 dict。"""
    calls = {"n": 0}

    @tool(registry=registry, description="查询订单物流")
    def query_order(order_id: str) -> str:
        calls["n"] += 1
        return f"订单 {order_id} 运输中"

    return calls


async def _tool_sequence(db: Database, run_id: str) -> list[str]:
    async with db.session() as session:
        events = await TraceRepository(session).list_events(run_id)
    return [
        str(event.payload.get("tool"))
        for event in events
        if event.type is TraceEventType.TOOL_CALL_STARTED
    ]


async def _session_history_len(db: Database, session_id: str) -> int:
    history = await SessionMemory(db).history(session_id, window_size=100)
    return len(history)


def _model_completed(text: str, tool_calls: list | None = None) -> TraceEvent:
    return TraceEvent(
        id="evt-x",
        run_id="run-x",
        type=TraceEventType.MODEL_CALL_COMPLETED,
        payload={
            "model_key": "fake-chat",
            "text": text,
            "tool_calls": tool_calls or [],
            "input_tokens": None,
            "output_tokens": None,
            "usage_known": False,
        },
        created_at=datetime.now(UTC),
    )


def _other_event() -> TraceEvent:
    return TraceEvent(
        id="evt-y",
        run_id="run-x",
        type=TraceEventType.TOOL_CALL_STARTED,
        payload={"tool": "query_order", "args": {}},
        created_at=datetime.now(UTC),
    )


def test_script_from_events_extracts_in_order() -> None:
    events = [
        _other_event(),
        _model_completed("", [{"name": "query_order", "args": {"order_id": "1"}, "id": "c1"}]),
        _other_event(),
        _model_completed("最终回答"),
    ]
    script = script_from_events(events)
    assert script == [
        {"tool_calls": [{"name": "query_order", "args": {"order_id": "1"}, "id": "c1"}]},
        "最终回答",
    ]


def test_script_from_events_rejects_redacted_args() -> None:
    """录制 args 含 [REDACTED]（H07 脱敏审计副本）→ 安全重放显式拒绝。"""
    events = [
        _model_completed(
            "", [{"name": "query_order", "args": {"token": "[REDACTED]"}, "id": "c1"}]
        )
    ]
    with pytest.raises(LubanError) as exc_info:
        script_from_events(events)
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert "[REDACTED]" in exc_info.value.message


def test_tool_results_from_events_orders_and_rejects_redacted() -> None:
    ok = TraceEvent(
        id="evt-1",
        run_id="run-x",
        type=TraceEventType.TOOL_CALL_COMPLETED,
        payload={"tool": "query_order", "result": "订单运输中", "duration_ms": 5},
        created_at=datetime.now(UTC),
    )
    failed = TraceEvent(
        id="evt-2",
        run_id="run-x",
        type=TraceEventType.TOOL_CALL_FAILED,
        payload={"tool": "query_order", "error": {"code": "TOOL_EXECUTION_FAILED"}},
        created_at=datetime.now(UTC),
    )
    redacted = TraceEvent(
        id="evt-3",
        run_id="run-x",
        type=TraceEventType.TOOL_CALL_COMPLETED,
        payload={"tool": "query_order", "result": {"phone": "[REDACTED]"}},
        created_at=datetime.now(UTC),
    )
    assert tool_results_from_events([ok, failed]) == [
        {"ok": True, "result": "订单运输中"},
        {"ok": False, "error": {"code": "TOOL_EXECUTION_FAILED"}},
    ]
    with pytest.raises(LubanError) as exc_info:
        tool_results_from_events([redacted])
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR


async def test_replay_safe_mode_zero_side_effects(
    db: Database, registry: Registry, settings, make_runtime
) -> None:
    """safe 重放：零真实 handler、共享映射/模型表不变、会话隔离、工具序列复现。"""
    calls = _tool_counter(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])

    runtime: AgentRuntime = make_runtime()
    original = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="查两个订单", session_id="sess-1")
    )
    assert original.output == "两单都查完了"
    assert calls["n"] == 2  # 原始 Run 真实执行了两次工具（SCRIPT 含两次 query_order）
    original_tools = await _tool_sequence(db, original.id)
    history_before = await _session_history_len(db, "sess-1")

    replayed = await replay_run(original.id, db=db, registry=registry, settings=settings)

    # 重放 Run：新 Run、终态完成、model_key 为重放专用局部模型、会话隔离
    assert replayed.id != original.id
    assert replayed.status is RunStatus.COMPLETED
    assert replayed.model_key == REPLAY_MODEL_KEY
    assert replayed.session_id is None
    assert replayed.output == original.output
    assert await _tool_sequence(db, replayed.id) == original_tools
    # 零真实 handler 调用（录制结果回喂）
    assert calls["n"] == 2
    # 原会话历史不被重放污染
    assert await _session_history_len(db, "sess-1") == history_before

    # 共享 brain mapping 不变（Run 局部注入，未改映射）
    async with db.session() as session:
        mapping = await ModelRepository(session).get_brain_mapping("dev", BrainProfile.SMART)
        replay_model = await ModelRepository(session).get_model(REPLAY_MODEL_KEY)
    assert mapping is not None and mapping.key == "fake-chat"
    # 不写 models 表（replay-fake 不落库，仅 Run.model_key 留痕）
    assert replay_model is None


async def test_replay_parallel_two_runs_isolated(
    db: Database, registry: Registry, settings
) -> None:
    """正常 Run 与两个安全重放并行：互不干扰、共享映射不变、零真实 handler。"""
    calls = _tool_counter(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])
    runtime = AgentRuntime(db=db, settings=settings, registry=registry)
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="查两个订单"))

    first, second = await asyncio.gather(
        replay_run(original.id, db=db, registry=registry, settings=settings),
        replay_run(original.id, db=db, registry=registry, settings=settings),
    )
    assert first.status is RunStatus.COMPLETED
    assert second.status is RunStatus.COMPLETED
    assert first.id != second.id != original.id
    assert calls["n"] == 2  # 两个重放零真实调用
    async with db.session() as session:
        mapping = await ModelRepository(session).get_brain_mapping("dev", BrainProfile.SMART)
    assert mapping is not None and mapping.key == "fake-chat"


async def test_replay_missing_run_raises(db: Database, registry: Registry, settings) -> None:
    with pytest.raises(LubanError) as exc_info:
        await replay_run("no-such-run", db=db, registry=registry, settings=settings)
    assert exc_info.value.code is ErrorCode.RUN_NOT_FOUND


async def test_replay_empty_script_explicit_failure(
    db: Database, registry: Registry, settings, make_runtime
) -> None:
    """原 Run 无模型调用记录（历史不足）→ 显式 VALIDATION_ERROR，不宣称确定性复现。"""
    _tool_counter(registry)
    # 空 script：模型首次调用即抛，Run 失败且无 MODEL_CALL_COMPLETED
    await seed_fake_brain(db, script=[])
    await create_test_agent(db, tools=["query_order"])
    runtime: AgentRuntime = make_runtime()
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="任意"))
    assert original.status is RunStatus.FAILED

    with pytest.raises(LubanError) as exc_info:
        await replay_run(original.id, db=db, registry=registry, settings=settings)
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert "历史不足" in exc_info.value.message


async def test_replay_missing_tool_results_fails_explicitly(
    db: Database, registry: Registry, settings, make_runtime
) -> None:
    """录制工具结果缺失（模型要调工具但无录制结果）→ 进 Runtime 前显式拒绝，零真实 handler。"""
    calls = _tool_counter(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])
    runtime: AgentRuntime = make_runtime()
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="查两个订单"))

    # 删除全部 TOOL_CALL_COMPLETED/FAILED 录制 → 录制完整性前置校验拒绝
    async with db.session() as session:
        await session.execute(
            sa_delete(TraceEventRow).where(
                TraceEventRow.run_id == original.id,
                TraceEventRow.type.in_(
                    [
                        TraceEventType.TOOL_CALL_COMPLETED.value,
                        TraceEventType.TOOL_CALL_FAILED.value,
                    ]
                ),
            )
        )
        await session.flush()

    with pytest.raises(LubanError) as exc_info:
        await replay_run(original.id, db=db, registry=registry, settings=settings)
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert "工具录制结果不足" in exc_info.value.message
    assert calls["n"] == 2  # 全程零真实 handler


async def test_replay_redacted_events_rejected_end_to_end(
    db: Database, registry: Registry, settings, make_runtime
) -> None:
    """库内录制事件 args 已脱敏（[REDACTED]）→ 重放显式拒绝（H07 边界落地）。"""
    _tool_counter(registry)
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "query_order", "args": {"order_id": "1"}, "id": "c1"}]},
            "好的",
        ],
    )
    await create_test_agent(db, tools=["query_order"])
    runtime: AgentRuntime = make_runtime()
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="查询"))
    assert original.status is RunStatus.COMPLETED

    # 模拟库内录制已被脱敏（TraceWriter 落库即审计副本）
    async with db.session() as session:
        rows = (
            await session.scalars(
                select(TraceEventRow).where(
                    TraceEventRow.run_id == original.id,
                    TraceEventRow.type == TraceEventType.MODEL_CALL_COMPLETED.value,
                )
            )
        ).all()
        payload = deepcopy(rows[0].payload)  # 深拷贝：共享嵌套对象会让 ORM 判定列未变
        payload["tool_calls"][0]["args"] = {"api_key": "[REDACTED]"}
        rows[0].payload = payload
        await session.flush()

    # H07：落库 payload 是脱敏审计副本，安全重放须显式拒绝
    with pytest.raises(LubanError) as exc_info:
        await replay_run(original.id, db=db, registry=registry, settings=settings)
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR


async def test_replay_live_mode_opt_in_real_handlers(
    db: Database, registry: Registry, settings
) -> None:
    """live 真实再执行：真实 brain 解析 + 真实 handler（显式 opt-in），会话同样隔离。"""
    calls = _tool_counter(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])
    runtime = AgentRuntime(db=db, settings=settings, registry=registry)
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="查两个订单"))

    replayed = await replay_run(
        original.id, db=db, registry=registry, settings=settings, mode="live"
    )
    assert replayed.status is RunStatus.COMPLETED
    assert replayed.model_key == "fake-chat"  # 真实 brain 解析
    assert replayed.session_id is None
    assert calls["n"] == 4  # live 真实执行了两次工具


async def test_replay_live_mode_high_risk_requires_reapproval(
    db: Database, registry: Registry, settings
) -> None:
    """live 模式高风险工具无 approval_handler → 调用显式拒绝、handler 零执行（重新审批闸门）。"""
    calls = {"n": 0}

    @tool(registry=registry, description="高危操作", risk=RiskLevel.HIGH)
    def dangerous(action: str) -> str:
        calls["n"] += 1
        return f"已执行 {action}"

    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "dangerous", "args": {"action": "refund"}, "id": "c1"}]},
            "done",
        ],
    )
    await create_test_agent(db, tools=["dangerous"])
    runtime = AgentRuntime(db=db, settings=settings, registry=registry)
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="执行"))
    assert original.status is RunStatus.COMPLETED
    assert calls["n"] == 0  # 高风险无审批器：原始 Run 也未执行 handler

    replayed = await replay_run(
        original.id, db=db, registry=registry, settings=settings, mode="live"
    )
    assert calls["n"] == 0  # live 重放同样被拒绝，不自动放行
    async with db.session() as session:
        events = await TraceRepository(session).list_events(replayed.id)
    failed_tools = [e for e in events if e.type is TraceEventType.TOOL_CALL_FAILED]
    assert failed_tools
    assert failed_tools[0].payload["error"]["code"] == "TOOL_APPROVAL_REQUIRED"


async def test_replay_unknown_mode_rejected(db: Database, registry: Registry, settings) -> None:
    with pytest.raises(LubanError) as exc_info:
        await replay_run(
            "run-x", db=db, registry=registry, settings=settings, mode="yolo"  # type: ignore[arg-type]
        )
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR


async def test_replayed_run_script_survives_parallel_consumption(
    db: Database, registry: Registry, settings
) -> None:
    """script 深拷贝进 fake 定义：两个并行重放各自消费各自的光标（无共享游标）。"""
    _tool_counter(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])
    runtime = AgentRuntime(db=db, settings=settings, registry=registry)
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="查两个订单"))

    first, second = await asyncio.gather(
        replay_run(original.id, db=db, registry=registry, settings=settings),
        replay_run(original.id, db=db, registry=registry, settings=settings),
    )
    outputs = sorted(
        [run.output for run in (first, second) if run.output is not None]
    )
    assert outputs == ["两单都查完了", "两单都查完了"]


async def test_replay_run_persists_new_run_record(
    db: Database, registry: Registry, settings
) -> None:
    """重放产生的新 Run 落库可审计（model_key=REPLAY_MODEL_KEY、独立 run_id）。"""
    _tool_counter(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])
    runtime = AgentRuntime(db=db, settings=settings, registry=registry)
    original = await runtime.run(RunRequest(agent_key="test-agent", user_input="查两个订单"))

    replayed = await replay_run(original.id, db=db, registry=registry, settings=settings)
    async with db.session() as session:
        stored = await RunRepository(session).get_run(replayed.id)
    assert stored is not None
    assert stored.model_key == REPLAY_MODEL_KEY
    assert stored.input.get("user_input") == "查两个订单"
