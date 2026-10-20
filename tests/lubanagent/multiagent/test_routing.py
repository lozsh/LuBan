"""分流团队三形态（行为契约 1/2）：委派 faq/委派 refund/直接答；子 Run 契约与 TeamState 累积。"""

from lubanagent.core import RunStatus
from lubanagent.persistence import Database, RunRepository

from .conftest import seed_triage_team


async def test_delegate_to_faq(make_team_runner, db: Database):
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "faq", "input": "你们退货政策是什么"}',
            '{"action": "answer", "answer": "退货政策：七天无理由（faq 已解答）"}',
        ],
        faq_script=["退货政策：七天无理由"],
    )
    result = await make_team_runner().run(team, "你们退货政策是什么")

    assert result.status == "completed"
    assert "七天无理由" in result.output["final_output"]

    # TeamState 累积：delegations 与 messages 轨迹
    delegations = result.output["delegations"]
    assert len(delegations) == 1
    assert delegations[0].agent_key == "faq"
    assert delegations[0].input == "你们退货政策是什么"
    assert delegations[0].output == "退货政策：七天无理由"
    roles = [message["role"] for message in result.output["messages"]]
    assert roles == ["supervisor", "faq", "supervisor"]

    # 行为契约 2：成员子 Run parent_run_id=编排 Run、绑成员当前版本
    async with db.session() as session:
        children = await RunRepository(session).list_runs("faq", session_id=result.thread_id)
    assert len(children) == 1
    child = children[0]
    assert child.parent_run_id == result.run_id
    assert child.agent_version == 1
    assert child.status == RunStatus.COMPLETED
    assert delegations[0].run_id == child.id

    # supervisor 每次 decide 也是编排 Run 的子 Run（绑 supervisor 自己的定义版本）
    async with db.session() as session:
        sup_runs = await RunRepository(session).list_runs("triage", session_id=result.thread_id)
    assert len(sup_runs) == 2
    assert all(run.parent_run_id == result.run_id for run in sup_runs)
    assert all(run.agent_version == 1 for run in sup_runs)

    # 编排 Run 本身是 graph:team:<name> 标记
    async with db.session() as session:
        orch = await RunRepository(session).get_run(result.run_id)
    assert orch.agent_key == "graph:team:triage-team"
    assert orch.status == RunStatus.COMPLETED


async def test_delegate_to_refund(make_team_runner, db: Database):
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "refund", "input": "我要退款"}',
            '{"action": "answer", "answer": "退款已受理，原路退回"}',
        ],
        faq_script=["（不应被调用）"],
        refund_script=["退款已受理，3 天内到账"],
    )
    result = await make_team_runner().run(team, "我要退款")

    assert result.status == "completed"
    assert "退款已受理" in result.output["final_output"]
    delegations = result.output["delegations"]
    assert [record.agent_key for record in delegations] == ["refund"]

    async with db.session() as session:
        refund_runs = await RunRepository(session).list_runs(
            "refund", session_id=result.thread_id
        )
        faq_runs = await RunRepository(session).list_runs("faq", session_id=result.thread_id)
    assert len(refund_runs) == 1
    assert refund_runs[0].parent_run_id == result.run_id
    assert faq_runs == []  # 未委派的成员不产生子 Run


async def test_direct_answer(make_team_runner, db: Database):
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "answer", "answer": "你好，有什么可以帮你？"}'],
        faq_script=["（不应被调用）"],
    )
    result = await make_team_runner().run(team, "你好")

    assert result.status == "completed"
    assert result.output["final_output"] == "你好，有什么可以帮你？"
    assert result.output["delegations"] == []
    roles = [message["role"] for message in result.output["messages"]]
    assert roles == ["supervisor"]

    async with db.session() as session:
        faq_runs = await RunRepository(session).list_runs("faq", session_id=result.thread_id)
    assert faq_runs == []


async def test_parse_failure_falls_back_to_answer(make_team_runner, db: Database):
    """supervisor 输出非 JSON → 容错解析当 answer 处理（不炸编排，契约决策 5）。"""
    team = await seed_triage_team(
        db,
        triage_script=["我直接回答你，不走 JSON。"],
        faq_script=["（不应被调用）"],
    )
    result = await make_team_runner().run(team, "随便聊聊")

    assert result.status == "completed"
    assert result.output["final_output"] == "我直接回答你，不走 JSON。"
    assert result.output["decision"]["reason"] == "parse_fallback"
    assert result.output["delegations"] == []
