"""评测运行器：真跑 Runtime + 读 DB Trace 断言；一 pass 一 fail → pass_rate=0.5 且不中断。"""

from lubanagent.core import ErrorCode
from lubanagent.model import ScriptedFakeChatModel
from lubanagent.observability.evals import (
    EvalCase,
    EvalDataset,
    EvalRunner,
    ExpectOutput,
    JudgeSpec,
    LlmJudge,
)
from lubanagent.persistence import Database
from lubanagent.runtime import AgentRuntime

from .conftest import create_test_agent, register_query_order, seed_fake_brain

SCRIPT = [
    {"tool_calls": [{"name": "query_order", "args": {"order_id": "12345"}, "id": "c1"}]},
    "先安抚您，订单 12345 运输中，给您替代方案",
]


def _dataset(*cases: EvalCase) -> EvalDataset:
    return EvalDataset(key="orders", cases=list(cases))


async def test_run_dataset_one_pass_one_fail(
    db: Database, registry, settings, make_runtime
) -> None:
    register_query_order(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])
    runtime: AgentRuntime = make_runtime()

    dataset = _dataset(
        EvalCase(
            case="正常查询",
            input="订单 12345 呢",
            expect_tools=["query_order"],
            expect_tool_args={"query_order": {"order_id": "12345"}},
            expect_output=ExpectOutput(contains=["安抚", "替代"]),
        ),
        EvalCase(
            case="缺关键字",
            input="随便问",
            expect_output=ExpectOutput(contains=["不存在的词"]),
        ),
    )
    report = await EvalRunner(runtime).run_dataset("test-agent", dataset)

    assert report.dataset_key == "orders"
    assert report.agent_key == "test-agent"
    assert report.agent_version == 1
    assert len(report.results) == 2
    assert report.results[0].passed is True
    assert report.results[0].run_id  # 真跑留 Run
    assert {v.kind for v in report.results[0].verdicts} == {"tool_call", "output"}
    # fail case 不中断整集，且带上失败断言明细
    assert report.results[1].passed is False
    assert any(not v.passed for v in report.results[1].verdicts)
    assert report.pass_rate == 0.5


async def test_run_dataset_case_exception_recorded_not_raised(
    db: Database, registry, settings, make_runtime
) -> None:
    await seed_fake_brain(db, script=["你好"])
    runtime: AgentRuntime = make_runtime()
    dataset = _dataset(EvalCase(case="不存在 agent", input="hi"))

    report = await EvalRunner(runtime).run_dataset("ghost-agent", dataset)

    assert len(report.results) == 1
    result = report.results[0]
    assert result.passed is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.AGENT_NOT_FOUND.value
    assert result.run_id == ""
    assert report.pass_rate == 0.0


async def test_run_dataset_with_judge(
    db: Database, registry, settings, make_runtime
) -> None:
    register_query_order(registry)
    await seed_fake_brain(db, script=SCRIPT)
    await create_test_agent(db, tools=["query_order"])
    judge = LlmJudge(
        # judge 模型独立脚本：每次判定返回 5 分
        ScriptedFakeChatModel(script=['{"score": 5, "reason": "先安抚再给方案"}'] * 2)
    )
    dataset = _dataset(
        EvalCase(
            case="judge 用例",
            input="订单呢",
            judge=JudgeSpec(criteria="先安抚情绪，再给解决方案", pass_score=4),
        ),
        EvalCase(case="judge 用例 2", input="再问", judge=JudgeSpec(criteria="安抚")),
    )
    report = await EvalRunner(make_runtime(), judge=judge).run_dataset("test-agent", dataset)

    assert all(result.passed for result in report.results)
    assert report.results[0].verdicts[0].kind == "judge"
    assert "5/4" in report.results[0].verdicts[0].detail
