"""可评测（行为契约 5）：编排 Run 及其子 Run 的 trace_events 可被 D2-1 check_tool_calls 消费。"""

from lubanagent.extension import Registry, tool
from lubanagent.observability.evals.assertions import check_tool_calls
from lubanagent.observability.evals.dataset import EvalCase
from lubanagent.persistence import Database, RunRepository, TraceRepository

from .conftest import seed_triage_team


async def test_team_trace_consumed_by_eval_assertion(
    make_team_runner, db: Database, registry: Registry
):
    @tool(registry=registry, description="查询政策")
    def lookup_policy(topic: str) -> str:
        return f"政策[{topic}]：七天无理由"

    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "faq", "input": "退货政策"}',
            '{"action": "answer", "answer": "退货政策：七天无理由"}',
        ],
        faq_script=[
            {"tool_calls": [{"name": "lookup_policy", "args": {"topic": "refund"}, "id": "c1"}]},
            "退货政策：七天无理由",
        ],
        faq_tools=["lookup_policy"],
    )
    result = await make_team_runner().run(team, "你们退货政策是什么")
    assert result.status == "completed"

    # 编排 Run + 全部子 Run 的 trace_events（成员工具调用事件落在成员子 Run 上）
    events = []
    async with db.session() as session:
        run_repo = RunRepository(session)
        trace_repo = TraceRepository(session)
        for run in await run_repo.list_runs(session_id=result.thread_id, limit=50):
            if run.id == result.run_id or run.parent_run_id == result.run_id:
                events.extend(await trace_repo.list_events(run.id))

    case = EvalCase(
        name="退货政策", input="你们退货政策是什么", expect_tools=["lookup_policy"]
    )
    verdict = check_tool_calls(case, events)
    assert verdict is not None
    assert verdict.passed, verdict.detail
