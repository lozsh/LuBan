"""防 ping-pong（行为契约 6）：互相委派达 max_delegations → 强制 answer（注明 reason）。"""

from lubanagent.persistence import Database, RunRepository

from .conftest import seed_triage_team


async def test_pingpong_stops_at_max_delegations(make_team_runner, db: Database):
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "faq", "input": "问 faq"}',
            '{"action": "delegate", "agent": "refund", "input": "问 refund"}',
        ],
        faq_script=["faq 答复"],
        refund_script=["refund 答复"],
        max_delegations=2,
    )
    result = await make_team_runner().run(team, "互相踢皮球")

    assert result.status == "completed"
    delegations = result.output["delegations"]
    assert [record.agent_key for record in delegations] == ["faq", "refund"]
    assert result.output["decision"]["reason"] == "max_delegations"
    assert "已达最大委派轮次" in result.output["final_output"]
    assert "refund 答复" in result.output["final_output"]  # 强制收尾带最近一次成员结果

    # 触顶后不再调模型：supervisor 子 Run 恰为 max_delegations 次（第 3 次 decide 直接强制收尾）
    async with db.session() as session:
        sup_runs = await RunRepository(session).list_runs("triage", session_id=result.thread_id)
    assert len(sup_runs) == 2


async def test_team_child_runs_inherit_user_id(make_team_runner, db: Database):
    """user_id 观测标签继承：父 ctx 带 user_id 时 supervisor 与成员子 Run 全部落同值。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "faq", "input": "问 faq"}',
            '{"action": "answer", "answer": "处理完毕"}',
        ],
        faq_script=["faq 答复"],
    )
    from lubanagent.core import BrainProfile, RunContext

    parent_ctx = RunContext(
        run_id="parent-run", agent_key="caller", agent_version=1, user_id="u-team",
        brain_profile=BrainProfile.SMART,
    )
    result = await make_team_runner().run(team, "问个问题", parent_ctx=parent_ctx)

    assert result.status == "completed"
    async with db.session() as session:
        repo = RunRepository(session)
        orch = await repo.get_run(result.run_id)
        sup_runs = await repo.list_runs("triage", session_id=result.thread_id)
        member_runs = await repo.list_runs("faq", session_id=result.thread_id)
    assert orch is not None and orch.user_id == "u-team"
    # supervisor 两次 decide（委派 + 收尾）各一条子 Run，均继承 user_id
    assert len(sup_runs) == 2 and {r.user_id for r in sup_runs} == {"u-team"}
    assert len(member_runs) == 1 and member_runs[0].user_id == "u-team"
