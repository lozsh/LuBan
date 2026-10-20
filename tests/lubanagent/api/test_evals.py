"""evals 路由：报告列表/详情读真实 eval_results（R5 评测体系打通）。"""

from lubanagent.core import (
    AssertionVerdict,
    CaseResult,
    EvalCase,
    EvalDataset,
    EvalReport,
    ExpectOutput,
)
from lubanagent.persistence import Database, EvalRepository


async def _seed_eval_report(db: Database) -> int:
    """经仓储种一条完整报告（模拟 `luban eval run` 落库产物）。"""
    async with db.session() as session:
        repo = EvalRepository(session)
        await repo.create_dataset(
            EvalDataset(
                key="cs-regression",
                name="客服回归集",
                cases=[
                    EvalCase(
                        name="打招呼",
                        input="你好",
                        expect_output=ExpectOutput(contains=["你好"]),
                    )
                ],
            )
        )
        return await repo.save_report(
            EvalReport(
                dataset_key="cs-regression",
                agent_key="cs-agent",
                agent_version=3,
                model_key="fake-chat",
                results=[
                    CaseResult(
                        case="打招呼",
                        passed=True,
                        verdicts=[
                            AssertionVerdict(
                                kind="output", passed=True, detail="输出包含全部关键字"
                            )
                        ],
                        run_id="run-1",
                        output="你好，很高兴为您服务",
                    ),
                    CaseResult(
                        case="退款",
                        passed=False,
                        verdicts=[
                            AssertionVerdict(
                                kind="refusal", passed=False, detail="应拒答未拒答（错答）"
                            )
                        ],
                        run_id="run-2",
                    ),
                ],
            )
        )


async def test_list_evals_empty(client) -> None:
    response = await client.get("/v1/evals")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_evals_returns_real_summaries(client, db) -> None:
    await _seed_eval_report(db)
    response = await client.get("/v1/evals")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    summary = body[0]
    assert summary["dataset_key"] == "cs-regression"
    assert summary["agent_key"] == "cs-agent"
    assert summary["agent_version"] == 3
    assert summary["model_key"] == "fake-chat"
    assert summary["pass_rate"] == 0.5
    assert (summary["passed_cases"], summary["total_cases"]) == (1, 2)
    assert summary["wrong_answer_rate"] == 1.0
    assert "created_at" in summary
    # 列表元素不含逐 case 明细
    assert "results" not in summary


async def test_list_evals_filters(client, db) -> None:
    await _seed_eval_report(db)
    ghost = await client.get("/v1/evals", params={"dataset_key": "ghost"})
    assert ghost.json() == []
    other = await client.get("/v1/evals", params={"agent_key": "other"})
    assert other.json() == []
    matched = await client.get("/v1/evals", params={"dataset_key": "cs-regression"})
    assert len(matched.json()) == 1


async def test_get_eval_detail_returns_case_results(client, db) -> None:
    result_id = await _seed_eval_report(db)
    response = await client.get(f"/v1/evals/{result_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == result_id
    assert detail["dataset_key"] == "cs-regression"
    assert detail["pass_rate"] == 0.5
    cases = detail["results"]
    assert [case["case"] for case in cases] == ["打招呼", "退款"]
    assert cases[0]["passed"] is True
    assert cases[0]["verdicts"][0]["kind"] == "output"
    assert cases[0]["verdicts"][0]["detail"] == "输出包含全部关键字"
    assert cases[0]["run_id"] == "run-1"
    assert cases[0]["output"] == "你好，很高兴为您服务"
    assert cases[1]["passed"] is False
    assert cases[1]["verdicts"][0]["kind"] == "refusal"


async def test_get_eval_not_found(client, db) -> None:
    response = await client.get("/v1/evals/9999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVAL_RESULT_NOT_FOUND"
