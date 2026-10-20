"""Skill/McpServer/Eval 仓储：领域对象承载的 CRUD（R5 后 Eval 亦领域对象化）。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    AssertionVerdict,
    CaseResult,
    ErrorCode,
    EvalCase,
    EvalDataset,
    EvalReport,
    ExpectOutput,
    LubanError,
    McpServer,
)
from lubanagent.persistence.repositories import (
    EvalRepository,
    McpServerRepository,
    SkillRepository,
)
from lubanagent.skill import SkillMeta, SkillPackage


async def test_skill_repository_crud(session: AsyncSession) -> None:
    repo = SkillRepository(session)
    package = SkillPackage(
        meta=SkillMeta(name="refund", description="退款技能", version="1.0.0"),
        path="/skills/refund",
        scripts=["scripts/run.py"],
        content_hash="hash-v1",
    )
    await repo.upsert(package)

    loaded = await repo.get("refund")
    assert loaded == package

    # 同 key 内容 hash 未变 -> version/manifest 不更新
    renamed = package.model_copy(
        update={"meta": SkillMeta(name="refund", description="退款技能", version="9.9.9")}
    )
    await repo.upsert(renamed)
    unchanged = await repo.get("refund")
    assert unchanged is not None
    assert unchanged.meta.version == "1.0.0"

    # 内容 hash 变化 -> 更新
    changed = package.model_copy(
        update={
            "meta": SkillMeta(name="refund", description="退款技能", version="1.1.0"),
            "content_hash": "hash-v2",
        }
    )
    await repo.upsert(changed)
    reloaded = await repo.get("refund")
    assert reloaded is not None
    assert reloaded.meta.version == "1.1.0"
    assert reloaded.content_hash == "hash-v2"

    assert await repo.get("ghost") is None
    assert [p.meta.name for p in await repo.list()] == ["refund"]


async def test_mcp_server_repository_crud(session: AsyncSession) -> None:
    repo = McpServerRepository(session)
    server = McpServer(
        key="fs",
        name="文件系统",
        transport="stdio",
        config={"command": "mcp-fs"},
    )
    await repo.upsert(server)

    loaded = await repo.get("fs")
    assert loaded == server  # 领域对象往返一致 (enabled 默认 True)

    await repo.upsert(
        server.model_copy(update={"transport": "http", "config": {"url": "http://x"}})
    )
    reloaded = await repo.get("fs")
    assert reloaded is not None and reloaded.transport == "http"

    assert await repo.get("ghost") is None
    assert [s.key for s in await repo.list()] == ["fs"]


async def test_eval_repository_flow(session: AsyncSession) -> None:
    repo = EvalRepository(session)
    dataset = EvalDataset(
        key="greet",
        name="问候集",
        cases=[
            EvalCase(name="打招呼", input="你好", expect_output=ExpectOutput(contains=["你好"]))
        ],
    )
    await repo.create_dataset(dataset, version="1.0.0")

    loaded = await repo.get_dataset("greet")
    assert loaded == dataset  # 领域对象往返（populate_by_name 兼容 name 键）
    assert await repo.get_dataset("ghost") is None

    report = EvalReport(
        dataset_key="greet",
        agent_key="demo",
        agent_version=2,
        model_key="fake-chat",
        results=[
            CaseResult(
                case="打招呼",
                passed=True,
                verdicts=[
                    AssertionVerdict(kind="output", passed=True, detail="输出包含全部关键字")
                ],
                run_id="run-1",
                output="你好呀",
            ),
            CaseResult(
                case="售后",
                passed=False,
                verdicts=[
                    AssertionVerdict(kind="refusal", passed=False, detail="应拒答未拒答（错答）")
                ],
                run_id="run-2",
            ),
        ],
    )
    result_id = await repo.save_report(report)

    summaries = await repo.list_summaries()
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.id == result_id
    assert summary.dataset_key == "greet"
    assert summary.agent_key == "demo"
    assert summary.agent_version == 2
    assert summary.model_key == "fake-chat"
    assert summary.pass_rate == 0.5
    assert (summary.passed_cases, summary.total_cases) == (1, 2)
    assert summary.wrong_answer_rate == 1.0

    # 详情回读：完整逐 case 结果（pass/fail、断言明细、judge 结论字段同构）
    detail = await repo.get_detail(result_id)
    assert detail is not None
    assert detail.results == report.results
    assert detail.results[0].verdicts[0].kind == "output"
    assert detail.results[1].verdicts[0].kind == "refusal"
    assert detail.results[1].verdicts[0].passed is False

    # 过滤：数据集/Agent 不匹配 → 空
    assert await repo.list_summaries(dataset_key="ghost") == []
    assert await repo.list_summaries(agent_key="other") == []
    assert await repo.get_detail(9999) is None


async def test_eval_save_report_missing_dataset_raises(session: AsyncSession) -> None:
    report = EvalReport(dataset_key="ghost", agent_key="demo", agent_version=1, results=[])
    with pytest.raises(LubanError) as exc_info:
        await EvalRepository(session).save_report(report)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
