"""路由策略演进（指定入口成员 + 扇出并行）与向后兼容。

- 入口成员：用户消息先进 entry_member，输出回 supervisor；执行计入委派上限。
- 扇出并行：supervisor 一次决策委派多成员，gather 并行执行、结果按声明顺序汇总；
  预算继承（共享池累计）、取消传播（并行子全停）与既有委派语义一致。
- 兼容：不声明新字段的多 Agent 团队行为零变化（含 fanout 未启用时 agents 列表被拒）。
"""

from typing import Any

import pytest

from lubanagent.core import (
    BrainProfile,
    Budget,
    BudgetExceededError,
    EndReason,
    ErrorCode,
    LubanError,
    RunCancelledError,
    RunContext,
    RunStatus,
)
from lubanagent.model import ScriptedFakeChatModel
from lubanagent.multiagent import TeamBuilder
from lubanagent.persistence import Database, RunRepository
from lubanagent.runtime import BudgetGuard, CancellationToken

from .conftest import seed_triage_team


def _parent_ctx(**kwargs: Any) -> RunContext:
    return RunContext(
        run_id="parent-run",
        agent_key="parent-agent",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        **kwargs,
    )


# ------------------------------------------------------------ 指定入口成员


async def test_entry_member_runs_before_supervisor(make_team_runner, db: Database):
    """用户消息先进入口成员（非 supervisor 直接纳管），其输出进入 supervisor 决策上下文。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "answer", "answer": "根据 faq 初答汇总：七天无理由"}'],
        faq_script=["faq 初答：七天无理由"],
        entry_member="faq",
    )
    result = await make_team_runner().run(team, "你们退货政策是什么")

    assert result.status == "completed"
    assert result.output["final_output"] == "根据 faq 初答汇总：七天无理由"
    # 入口成员执行计入 delegations，且是第一条
    delegations = result.output["delegations"]
    assert [record.agent_key for record in delegations] == ["faq"]
    assert delegations[0].input == "你们退货政策是什么"  # 入口成员吃原始用户输入
    assert delegations[0].output == "faq 初答：七天无理由"
    roles = [message["role"] for message in result.output["messages"]]
    assert roles == ["faq", "supervisor"]  # faq 先于 supervisor 出现在轨迹

    async with db.session() as session:
        repo = RunRepository(session)
        faq_runs = await repo.list_runs("faq", session_id=result.thread_id)
        sup_runs = await repo.list_runs("triage", session_id=result.thread_id)
    # 入口成员子 Run 同一委派契约：parent_run_id=编排 Run、COMPLETED
    assert len(faq_runs) == 1
    assert faq_runs[0].parent_run_id == result.run_id
    assert faq_runs[0].status == RunStatus.COMPLETED
    assert delegations[0].run_id == faq_runs[0].id
    # supervisor 只 decide 一次，且决策 prompt 已看到入口成员输出（拿它汇总）
    assert len(sup_runs) == 1
    assert sup_runs[0].parent_run_id == result.run_id
    assert "faq 初答：七天无理由" in sup_runs[0].input["user_input"]


async def test_entry_member_then_supervisor_delegates(make_team_runner, db: Database):
    """入口成员输出回 supervisor 后仍可继续委派其他成员（拓扑闭环）。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "refund", "input": "用户要退款"}',
            '{"action": "answer", "answer": "已按入口分流结果受理退款"}',
        ],
        faq_script=["faq 初答：建议退款"],
        refund_script=["退款已受理"],
        entry_member="faq",
    )
    result = await make_team_runner().run(team, "买的东西有问题")

    assert result.status == "completed"
    assert [record.agent_key for record in result.output["delegations"]] == ["faq", "refund"]
    roles = [message["role"] for message in result.output["messages"]]
    assert roles == ["faq", "supervisor", "refund", "supervisor"]


async def test_entry_member_counts_toward_max_delegations(
    make_team_runner, db: Database
):
    """入口成员执行计入委派上限：max_delegations=1 时 supervisor 触顶直接强制 answer。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "delegate", "agent": "refund", "input": "x"}'],
        faq_script=["faq 初答"],
        max_delegations=1,
        entry_member="faq",
    )
    result = await make_team_runner().run(team, "hi")

    assert result.status == "completed"
    assert result.output["decision"]["reason"] == "max_delegations"
    assert "已达最大委派轮次" in result.output["final_output"]
    assert "faq 初答" in result.output["final_output"]  # 强制收尾带最近成员结果
    # 触顶不再调模型：supervisor 子 Run 为 0（入口成员执行后即触顶）
    async with db.session() as session:
        sup_runs = await RunRepository(session).list_runs("triage", session_id=result.thread_id)
    assert sup_runs == []


def test_entry_member_must_be_registered_member():
    """入口成员未登记（或误填 supervisor）→ VALIDATION_ERROR。"""
    with pytest.raises(LubanError) as exc_info:
        (
            TeamBuilder("t")
            .supervisor("triage")
            .agent("faq")
            .route(entry_member="ghost")
            .build()
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR

    with pytest.raises(LubanError) as exc_info:
        (
            TeamBuilder("t")
            .supervisor("triage")
            .agent("faq")
            .route(entry_member="triage")  # supervisor 不是成员
            .build()
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


# ------------------------------------------------------------ 扇出并行


async def test_fanout_both_members_execute_and_reach_supervisor(
    make_team_runner, db: Database
):
    """一次决策委派两成员：都执行、都是编排 Run 的子 Run、结果按声明顺序回 supervisor。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agents": ["faq", "refund"], "input": "同时查政策和退款"}',
            '{"action": "answer", "answer": "汇总：七天无理由；退款 3 天到账"}',
        ],
        faq_script=["政策：七天无理由"],
        refund_script=["退款：3 天到账"],
        fanout=True,
    )
    result = await make_team_runner().run(team, "退货且退款")

    assert result.status == "completed"
    assert result.output["final_output"] == "汇总：七天无理由；退款 3 天到账"
    # 扇出一轮 = 两条 DelegationRecord，按声明顺序
    delegations = result.output["delegations"]
    assert [record.agent_key for record in delegations] == ["faq", "refund"]
    assert delegations[0].input == "同时查政策和退款"
    messages = result.output["messages"]
    roles = [message["role"] for message in messages]
    assert roles == ["supervisor", "faq", "refund", "supervisor"]
    assert messages[0]["content"] == "委派 faq、refund：同时查政策和退款"  # 扇出决策留痕

    # 两个并行子 Run：父子契约与既有单成员委派一致
    async with db.session() as session:
        repo = RunRepository(session)
        faq_runs = await repo.list_runs("faq", session_id=result.thread_id)
        refund_runs = await repo.list_runs("refund", session_id=result.thread_id)
    assert len(faq_runs) == 1 and len(refund_runs) == 1
    for child in (faq_runs[0], refund_runs[0]):
        assert child.parent_run_id == result.run_id
        assert child.status == RunStatus.COMPLETED
    assert {delegations[0].run_id, delegations[1].run_id} == {faq_runs[0].id, refund_runs[0].id}


async def test_fanout_shared_budget_accumulates(
    make_team_runner, db: Database, sequential_models
):
    """并行子 Run 共享团队预算池：两成员 + 两次 decide 的消费全部累计进同一 guard。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agents": ["faq", "refund"], "input": "x"}',
            '{"action": "answer", "answer": "已汇总"}',
        ],
        faq_script=["faq 答复"],
        refund_script=["refund 答复"],
        fanout=True,
    )
    sequential_models.set_usage("fake-smart", (10, 10))
    sequential_models.set_usage("fake-fast", (10, 10))
    sequential_models.set_usage("fake-cheap", (10, 10))
    guard = BudgetGuard(Budget(max_tokens=1000), timeout_seconds=300.0)

    result = await make_team_runner().run(
        team, "hi", parent_ctx=_parent_ctx(metadata={"budget_guard": guard})
    )

    assert result.status == "completed"
    # 2 次 supervisor decide + 2 个并行成员 = 4 次模型调用 × 20 tokens，同一池累计
    assert guard.usage.model_calls == 4
    assert guard.usage.total == 80


async def test_fanout_budget_exceeded_fails_orchestration(
    make_team_runner, db: Database, sequential_models
):
    """共享池撞线 → 编排 Run 终态 BUDGET_EXCEEDED（并行消费不允许烧完继续）。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "delegate", "agents": ["faq", "refund"], "input": "x"}'],
        faq_script=["faq 答复"],
        refund_script=["refund 答复"],
        fanout=True,
    )
    sequential_models.set_usage("fake-smart", (10, 10))
    sequential_models.set_usage("fake-fast", (10, 10))
    sequential_models.set_usage("fake-cheap", (10, 10))
    guard = BudgetGuard(Budget(max_tokens=50), timeout_seconds=300.0)  # 2 调用后撞线

    with pytest.raises(BudgetExceededError):
        await make_team_runner().run(
            team, "hi", parent_ctx=_parent_ctx(metadata={"budget_guard": guard})
        )

    assert guard.usage.total > 50  # 撞线即停，已消费全部累计进池
    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:team:triage-team")
    assert runs[0].status == RunStatus.FAILED
    assert runs[0].end_reason == EndReason.BUDGET_EXCEEDED


class _CancellingFakeChatModel(ScriptedFakeChatModel):
    """每次模型调用后取消父 token，构造扇出前中途取消的确定性场景。"""

    token: Any = None

    def _next(self) -> Any:
        item = super()._next()
        if self.token is not None:
            self.token.cancel()
        return item


async def test_fanout_cancel_propagates_to_parallel_children(
    make_team_runner, db: Database, monkeypatch
):
    """supervisor 扇出决策后父 token 取消 → 成员节点边界停：并行子全停（无任何成员子 Run）。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "delegate", "agents": ["faq", "refund"], "input": "x"}'],
        faq_script=["（不应执行）"],
        refund_script=["（不应执行）"],
        fanout=True,
    )
    token = CancellationToken()

    def factory(definition):
        return _CancellingFakeChatModel(
            script=list(definition.params.get("script", [])), token=token
        )

    monkeypatch.setattr("lubanagent.runtime.agent_loop.create_chat_model", factory)

    with pytest.raises(RunCancelledError):
        await make_team_runner().run(
            team, "hi", parent_ctx=_parent_ctx(metadata={"cancellation_token": token})
        )

    async with db.session() as session:
        repo = RunRepository(session)
        runs = await repo.list_runs("graph:team:triage-team")
        faq_runs = await repo.list_runs("faq")
        refund_runs = await repo.list_runs("refund")
    assert runs[0].status == RunStatus.CANCELLED
    assert faq_runs == []  # 并行子全停：取消在成员节点边界生效
    assert refund_runs == []


async def test_fanout_unknown_member_falls_back_to_answer(
    make_team_runner, db: Database
):
    """扇出列表含未知成员 → 与单成员未知同路：降级 answer（reason=unknown_agent）。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agents": ["faq", "ghost"], "input": "x"}'
        ],
        faq_script=["（不应执行）"],
        refund_script=["（不应执行）"],
        fanout=True,
    )
    result = await make_team_runner().run(team, "hi")

    assert result.status == "completed"
    assert result.output["decision"]["reason"] == "unknown_agent"
    assert result.output["delegations"] == []
    async with db.session() as session:
        repo = RunRepository(session)
        assert await repo.list_runs("faq") == []
        assert await repo.list_runs("refund") == []


async def test_fanout_deduplicates_members(make_team_runner, db: Database):
    """扇出列表重复成员 → 去重保序，同一成员一轮只执行一次。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agents": ["faq", "faq"], "input": "x"}',
            '{"action": "answer", "answer": "ok"}',
        ],
        faq_script=["faq 答复"],
        fanout=True,
    )
    result = await make_team_runner().run(team, "hi")

    assert result.status == "completed"
    assert [record.agent_key for record in result.output["delegations"]] == ["faq"]
    assert result.output["messages"][0]["content"] == "委派 faq：x"  # 去重后单成员留痕
    async with db.session() as session:
        faq_runs = await RunRepository(session).list_runs("faq", session_id=result.thread_id)
    assert len(faq_runs) == 1


def test_fanout_parse_unit():
    """解析单元：fanout 启用 → agents 列表（去重保序）/ 未知成员降级；未启用 → 不进扇出分支。"""
    from lubanagent.multiagent import SupervisorRouter

    enabled = SupervisorRouter(agent_key="s", members=["faq", "refund"], fanout=True)
    decision = enabled._parse(
        '{"action": "delegate", "agents": ["refund", "faq", "refund"], "input": "x"}'
    )
    assert decision.action == "delegate"
    assert decision.agents == ["refund", "faq"]  # 去重保序
    assert decision.agent is None
    unknown = enabled._parse('{"action": "delegate", "agents": ["faq", "ghost"], "input": "x"}')
    assert unknown.reason == "unknown_agent"
    empty = enabled._parse('{"action": "delegate", "agents": [], "input": "x"}')
    assert empty.reason == "unknown_agent"

    disabled = SupervisorRouter(agent_key="s", members=["faq", "refund"], fanout=False)
    fallback = disabled._parse('{"action": "delegate", "agents": ["faq", "refund"], "input": "x"}')
    assert fallback.action == "answer"
    assert fallback.reason == "unknown_agent"


# ------------------------------------------------------------ 向后兼容


async def test_fanout_disabled_rejects_agents_list(make_team_runner, db: Database):
    """未启用 fanout：supervisor 即便输出 agents 列表也走老解析（未知成员 → answer），零变化。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agents": ["faq", "refund"], "input": "x"}'
        ],
        faq_script=["（不应执行）"],
        refund_script=["（不应执行）"],
        fanout=False,
    )
    result = await make_team_runner().run(team, "hi")

    assert result.status == "completed"
    assert result.output["decision"]["reason"] == "unknown_agent"
    assert result.output["delegations"] == []
    async with db.session() as session:
        repo = RunRepository(session)
        assert await repo.list_runs("faq") == []
        assert await repo.list_runs("refund") == []


async def test_default_route_policy_is_legacy_shape(db: Database):
    """不声明 entry_member/fanout 的团队：字段缺省、图入口仍是 supervisor（老行为）。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "answer", "answer": "x"}'],
        faq_script=["y"],
    )
    assert team.entry_member is None
    assert team.fanout is False
    assert team.flow.builder.structure().entry == "supervisor"


async def test_entry_member_shape_on_team_and_graph(db: Database):
    """声明路由策略后：Team 携带字段、图入口切到 entry-member 节点。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "answer", "answer": "x"}'],
        faq_script=["y"],
        refund_script=["z"],
        entry_member="faq",
        fanout=True,
    )
    assert team.entry_member == "faq"
    assert team.fanout is True
    structure = team.flow.builder.structure()
    assert structure.entry == "entry-member"
    assert {edge.from_node for edge in structure.edges} == {
        "entry-member",
        "member",
    }  # entry-member → supervisor、member → supervisor
