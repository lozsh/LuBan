"""退款流水线集成测试（行为契约 1/2）：小额直通、大额 interrupt → resume 走完。"""

import sqlalchemy as sa

from lubanagent.core import RunStatus
from lubanagent.graph import GraphBuilder
from lubanagent.persistence import Database, RunRepository
from lubanagent.persistence.models.checkpoint import CheckpointRow


def build_refund_flow() -> "object":
    """资格校验 → 金额计算 → when(>500 → 人工 approve，否则 refund) → 退款 → 通知。"""

    def check_eligibility(state):
        assert state["order_id"], "缺 order_id"
        return {"eligible": True}

    def calc_amount(state):
        return {"amount": state["amount"]}

    def do_refund(state):
        return {"refunded": True, "refund_amount": state["amount"]}

    def notify(state):
        return {"notified": f"refund:{state['refund_amount']}"}

    return (
        GraphBuilder("refund", description="退款流水线")
        .step("check", check_eligibility)
        .step("calc", calc_amount)
        .when("calc", lambda s: s["amount"] > 500, then="approve", otherwise="refund")
        .human("approve", prompt="大额退款需人工审批")
        .step("refund", do_refund)
        .step("notify", notify)
        .entry("check")
        .edge("check", "calc")
        .edge("approve", "refund")
        .edge("refund", "notify")
        .build()
    )


async def test_small_amount_goes_straight_through(make_runner):
    flow = build_refund_flow()
    runner = make_runner()
    result = await runner.run(flow, {"order_id": "o-1", "amount": 100})
    assert result.status == "completed"
    assert result.interrupt is None
    assert result.output["refunded"] is True
    assert result.output["refund_amount"] == 100
    assert result.output["notified"] == "refund:100"
    assert "approve" not in result.output  # 小额不走人工节点


async def test_large_amount_interrupts_then_resume_completes(make_runner, db: Database):
    flow = build_refund_flow()
    runner = make_runner()
    result = await runner.run(flow, {"order_id": "o-2", "amount": 800})
    # 大额在 human 节点 interrupted，payload 带节点名与提示
    assert result.status == "interrupted"
    assert result.interrupt["node"] == "approve"
    assert result.interrupt["prompt"] == "大额退款需人工审批"
    assert result.interrupt["state"]["amount"] == 800
    assert "refunded" not in result.output  # 未走到退款

    # 行为契约 2：暂停时 checkpoint 已落库
    async with db.session() as session:
        rows = (
            await session.execute(
                sa.select(CheckpointRow).where(CheckpointRow.thread_id == result.thread_id)
            )
        ).scalars().all()
    assert rows, "interrupted 时 checkpoints 表应有记录"

    # 图 Run 处于 WAITING_APPROVAL（等待人工）
    async with db.session() as session:
        run = await RunRepository(session).get_run(result.run_id)
    assert run.status == RunStatus.WAITING_APPROVAL

    # resume 后走完 refund → notify
    resumed = await runner.resume(flow, result.thread_id, {"approved": True})
    assert resumed.status == "completed"
    assert resumed.run_id == result.run_id  # 同一条 Run 续跑
    assert resumed.output["approve"] == {"approved": True}
    assert resumed.output["refunded"] is True
    assert resumed.output["notified"] == "refund:800"

    async with db.session() as session:
        run = await RunRepository(session).get_run(result.run_id)
    assert run.status == RunStatus.COMPLETED
    assert run.agent_key == "graph:refund"
    assert run.agent_version == 0
