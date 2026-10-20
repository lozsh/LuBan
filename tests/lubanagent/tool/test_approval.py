"""审批管理器: 发起/等待/决定/超时过期、跨实例恢复（R11）与执行器接线。"""

import asyncio
from datetime import datetime

import pytest

from lubanagent.core import ErrorCode, LubanError, RiskLevel
from lubanagent.extension import Registry, tool
from lubanagent.persistence import Base, Database, DbApprovalStore
from lubanagent.tool import ApprovalManager, AutoApproveManager, ToolExecutor


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/approval.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


def _descriptor(registry: Registry, **kwargs: object):
    @tool(registry=registry, **kwargs)  # type: ignore[arg-type]
    def op(target: str) -> str:
        return f"op:{target}"

    return registry.get_tool("op").descriptor


async def test_create_request_and_get_and_pending() -> None:
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = ApprovalManager()
    request = await manager.create_request("run-1", descriptor, {"target": "order-1"})
    assert request.run_id == "run-1"
    assert request.tool_key == "op"
    assert request.args == {"target": "order-1"}
    assert request.status == "pending"
    assert len(request.id) == 32
    assert await manager.get(request.id) == request
    assert await manager.get("missing") is None
    assert await manager.pending() == [request]
    assert await manager.pending(run_id="run-1") == [request]
    assert await manager.pending(run_id="other") == []


async def test_decide_approves_and_waiter_resumes() -> None:
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = ApprovalManager()
    request = await manager.create_request("run-1", descriptor, {"target": "x"})

    waiter = asyncio.create_task(manager.wait_for_decision(request.id))
    await asyncio.sleep(0)
    decided = await manager.decide(request.id, True, decided_by="admin")
    assert decided.status == "approved"
    assert await waiter is True
    stored = await manager.get(request.id)
    assert stored.status == "approved"
    assert stored.decided_by == "admin"
    assert isinstance(stored.decided_at, datetime)


async def test_decide_rejects_returns_false() -> None:
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = ApprovalManager()
    request = await manager.create_request("run-1", descriptor, {"target": "x"})

    waiter = asyncio.create_task(manager.wait_for_decision(request.id))
    await asyncio.sleep(0)
    await manager.decide(request.id, False)
    assert await waiter is False
    assert (await manager.get(request.id)).status == "rejected"


async def test_wait_timeout_marks_expired_and_returns_false() -> None:
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = ApprovalManager()
    request = await manager.create_request("run-1", descriptor, {"target": "x"})
    assert await manager.wait_for_decision(request.id, timeout_seconds=0.02) is False
    expired = await manager.get(request.id)
    assert expired.status == "expired"
    assert expired.decided_by is None
    assert isinstance(expired.decided_at, datetime)


async def test_wait_unknown_id_raises() -> None:
    manager = ApprovalManager()
    with pytest.raises(LubanError) as exc_info:
        await manager.wait_for_decision("nope")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_decide_unknown_or_terminal_raises() -> None:
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = ApprovalManager()
    with pytest.raises(LubanError) as exc_info:
        await manager.decide("nope", True)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR

    request = await manager.create_request("run-1", descriptor, {"target": "x"})
    await manager.decide(request.id, True)
    decided = await manager.get(request.id)
    with pytest.raises(LubanError):
        await manager.decide(request.id, False, decided_by="forged")
    assert await manager.get(request.id) == decided


async def test_expired_approval_does_not_execute_tool() -> None:
    reg = Registry()
    calls: list[str] = []

    @tool(registry=reg, approval_required=True)
    async def danger(target: str) -> str:
        calls.append(target)
        return "executed"

    manager = ApprovalManager(default_timeout_seconds=0.01)
    executor = ToolExecutor(approval_handler=manager.as_handler("run-expired"))
    result = await executor.execute(reg.get_tool("danger"), {"target": "x"})
    assert result.success is False
    assert calls == []
    expired = (await manager.list_all(run_id="run-expired"))[0]
    assert expired.status == "expired"
    assert expired.decided_by is None
    with pytest.raises(LubanError):
        await manager.decide(expired.id, True, decided_by="late-operator")
    assert await manager.get(expired.id) == expired
    assert calls == []


async def test_auto_approve_is_system_attributed_and_terminal_decision_is_respected() -> None:
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = AutoApproveManager()
    pending = await manager.create_request("run-auto", descriptor, {})
    assert await manager.wait_for_decision(pending.id) is True
    resolved = await manager.get(pending.id)
    assert resolved.status == "approved"
    assert resolved.decided_by == "auto-approve"
    assert isinstance(resolved.decided_at, datetime)
    with pytest.raises(LubanError):
        await manager.decide(pending.id, False)
    assert await manager.wait_for_decision(pending.id) is True


async def test_default_manager_is_process_local_no_cross_instance_visibility() -> None:
    """缺省（内存 store）保持进程局部语义：新实例不认识旧审批，显式拒绝不静默放行。"""
    reg = Registry()
    descriptor = _descriptor(reg)
    manager_a = ApprovalManager()
    request = await manager_a.create_request("run-1", descriptor, {"target": "x"})

    manager_b = ApprovalManager()  # 新实例（进程重启等价）
    assert await manager_b.pending() == []
    with pytest.raises(LubanError) as decide_exc:
        await manager_b.decide(request.id, True, decided_by="other-instance")
    assert decide_exc.value.code == ErrorCode.VALIDATION_ERROR
    with pytest.raises(LubanError) as wait_exc:
        await manager_b.wait_for_decision(request.id)
    assert wait_exc.value.code == ErrorCode.VALIDATION_ERROR
    # 原实例的审批状态不受影响，仍等原实例决定
    assert (await manager_a.get(request.id)).status == "pending"


async def test_db_store_survives_manager_restart(db) -> None:
    """R11：审批落库后进程重启不丢——新 manager（≈重启）可列出、可决定；
    原进程 waiter 经轮询在 poll_interval 内感知并恢复，工具继续执行。"""
    reg = Registry()
    descriptor = _descriptor(reg)
    manager_a = ApprovalManager(store=DbApprovalStore(db), poll_interval_seconds=0.02)
    request = await manager_a.create_request("run-1", descriptor, {"target": "x"})

    waiter = asyncio.create_task(manager_a.wait_for_decision(request.id))
    await asyncio.sleep(0.01)

    manager_b = ApprovalManager(store=DbApprovalStore(db), poll_interval_seconds=0.02)
    pending = await manager_b.pending(run_id="run-1")
    assert [item.id for item in pending] == [request.id]  # 重启后仍可见

    decided = await manager_b.decide(request.id, True, decided_by="restarted-instance")
    assert decided.status == "approved"
    assert await asyncio.wait_for(waiter, timeout=2.0) is True  # 轮询兜底恢复

    stored = await manager_a.get(request.id)
    assert stored.status == "approved"
    assert stored.decided_by == "restarted-instance"


async def test_db_store_decide_is_atomic_under_race(db) -> None:
    """并发双 decide 恰一个成功：try_resolve 条件更新裁决，后到者显式 VALIDATION_ERROR。"""
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = ApprovalManager(store=DbApprovalStore(db))
    request = await manager.create_request("run-race", descriptor, {"target": "x"})

    outcomes = await asyncio.gather(
        manager.decide(request.id, True, decided_by="op-a"),
        manager.decide(request.id, False, decided_by="op-b"),
        return_exceptions=True,
    )
    errors = [item for item in outcomes if isinstance(item, LubanError)]
    resolved = [item for item in outcomes if not isinstance(item, BaseException)]
    assert len(errors) == 1
    assert errors[0].code == ErrorCode.VALIDATION_ERROR
    assert len(resolved) == 1
    stored = await manager.get(request.id)
    assert stored.status in ("approved", "rejected")
    assert stored.decided_by in ("op-a", "op-b")


async def test_db_store_persists_decided_records_for_audit(db) -> None:
    """终态记录不消失（list_all 审计回查），pending 视图正确收敛。"""
    reg = Registry()
    descriptor = _descriptor(reg)
    manager = ApprovalManager(store=DbApprovalStore(db))
    first = await manager.create_request("run-audit", descriptor, {"target": "1"})
    second = await manager.create_request("run-audit", descriptor, {"target": "2"})
    await manager.decide(first.id, True, decided_by="op")
    await manager.decide(second.id, False)

    assert await manager.pending(run_id="run-audit") == []
    all_records = await manager.list_all(run_id="run-audit")
    assert [item.status for item in all_records] == ["approved", "rejected"]
    assert all(item.decided_at is not None for item in all_records)


async def test_as_handler_suspends_execute_until_decide() -> None:
    """execute 在审批闸门挂起; 另一任务 decide(True) 后恢复并返回成功值。"""
    reg = Registry()

    @tool(registry=reg, approval_required=True)
    async def refund(order_id: str) -> str:
        return f"refunded:{order_id}"

    registered = reg.get_tool("refund")
    manager = ApprovalManager()
    executor = ToolExecutor(approval_handler=manager.as_handler("run-42"))

    task = asyncio.create_task(executor.execute(registered, {"order_id": "A1"}))
    await asyncio.sleep(0.05)
    assert not task.done()  # 实证: execute 挂起在闸门处

    pending = await manager.pending(run_id="run-42")
    assert len(pending) == 1
    assert pending[0].tool_key == "refund"
    assert pending[0].args == {"order_id": "A1"}

    await manager.decide(pending[0].id, True)
    result = await task
    assert result.success is True
    assert result.value == "refunded:A1"


async def test_as_handler_rejection_blocks_execution() -> None:
    reg = Registry()

    calls: list[str] = []

    @tool(registry=reg, risk=RiskLevel.HIGH)
    async def danger(target: str) -> str:
        calls.append(target)
        return "should-not-run"

    registered = reg.get_tool("danger")
    manager = ApprovalManager()
    executor = ToolExecutor(approval_handler=manager.as_handler("run-1"))

    task = asyncio.create_task(executor.execute(registered, {"target": "x"}))
    await asyncio.sleep(0.05)
    pending = await manager.pending(run_id="run-1")
    await manager.decide(pending[0].id, False)
    result = await task
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_APPROVAL_REQUIRED.value
    assert result.error["details"]["decision"] == "rejected"
    assert calls == []
    rejected = await manager.get(pending[0].id)
    with pytest.raises(LubanError):
        await manager.decide(rejected.id, True, decided_by="late-operator")
    assert calls == []
