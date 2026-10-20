"""评测领域模型：数据集/断言 verdict/报告与对比（契约 D2-1，R5 评测体系打通）。

模型自 observability/evals 下沉 core：persistence 仓储需要领域对象承载
（SkillRepository 同款全量 JSON 模式），而分层契约（importlinter）禁止
persistence → observability；observability/evals 原模块路径保留 re-export，
既有导入零改动。仅依赖 pydantic 与标准库（core 分层约束）。
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExpectOutput(BaseModel):
    """输出断言：contains 关键字全部出现（大小写不敏感）才 pass。"""

    contains: list[str] = Field(default_factory=list)


class JudgeSpec(BaseModel):
    """LLM-as-judge 配置：criteria 评分标准，pass_score 及格线（1-5，>= 为 pass）。"""

    criteria: str
    pass_score: int = 4


class EvalCase(BaseModel):
    """单条评测用例；四类断言（工具/输出/judge/拒答）均未配置时只校验 run 完成。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(validation_alias="case")
    input: str
    expect_tools: list[str] = Field(default_factory=list)
    expect_tool_args: dict[str, dict] = Field(default_factory=dict)
    expect_output: ExpectOutput | None = None
    judge: JudgeSpec | None = None
    # 防错答硬指标：True = 本用例必须拒答（Run 事件流含 KNOWLEDGE_NO_HIT）；
    # 未拒答即错答（fail），确定性判定不依赖 judge
    expect_refusal: bool = False


class EvalDataset(BaseModel):
    key: str
    name: str = ""
    cases: list[EvalCase]


class AssertionVerdict(BaseModel):
    """一条断言结果；error 非空表示判定过程出错（中立，不计 pass/fail）。"""

    kind: Literal["tool_call", "output", "judge", "refusal"]
    passed: bool
    detail: str = ""
    error: str | None = None


class CaseResult(BaseModel):
    case: str
    passed: bool
    verdicts: list[AssertionVerdict] = Field(default_factory=list)
    run_id: str
    output: str | None = None
    error: dict | None = None


class EvalReport(BaseModel):
    dataset_key: str
    agent_key: str
    agent_version: int
    # 本次评测实际命中的模型键（CLI 落库前回填；runner 本身不知道模型解析结果）
    model_key: str = ""
    results: list[CaseResult]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        passed = sum(1 for result in self.results if result.passed)
        return passed / len(self.results)

    @property
    def wrong_answer_rate(self) -> float:
        """错答率（防错答硬指标，目标 0）：应拒答用例中未拒答的比例。

        应拒答用例 = verdicts 含 kind="refusal" 的用例（expect_refusal=True）；
        错答 = 该 refusal verdict 判 fail（事件流无 knowledge_no_hit，模型在低置信检索下作答）。
        """
        refusal_results = [
            result
            for result in self.results
            if any(verdict.kind == "refusal" for verdict in result.verdicts)
        ]
        if not refusal_results:
            return 0.0
        wrong = sum(
            1
            for result in refusal_results
            if any(
                verdict.kind == "refusal" and not verdict.passed
                for verdict in result.verdicts
            )
        )
        return wrong / len(refusal_results)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, data: str) -> "EvalReport":
        return cls.model_validate_json(data)


class ComparisonReport(BaseModel):
    baseline: EvalReport
    current: EvalReport
    regressions: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    pass_rate_delta: float = 0.0


def compare_reports(baseline: EvalReport, current: EvalReport) -> ComparisonReport:
    """按用例名对齐：基线 pass 本次 fail → 回归；基线 fail 本次 pass → 改进。"""
    current_by_case = {result.case: result for result in current.results}
    baseline_by_case = {result.case: result for result in baseline.results}
    regressions = [
        name
        for name, base in baseline_by_case.items()
        if base.passed and name in current_by_case and not current_by_case[name].passed
    ]
    improvements = [
        name
        for name, base in baseline_by_case.items()
        if not base.passed and name in current_by_case and current_by_case[name].passed
    ]
    return ComparisonReport(
        baseline=baseline,
        current=current,
        regressions=regressions,
        improvements=improvements,
        pass_rate_delta=current.pass_rate - baseline.pass_rate,
    )


class EvalReportSummary(BaseModel):
    """eval_results 行的列表视图：聚合指标 + 定位键，不含逐 case 明细（API 列表响应）。"""

    id: int
    dataset_key: str
    agent_key: str
    agent_version: int
    model_key: str
    pass_rate: float
    passed_cases: int
    total_cases: int
    wrong_answer_rate: float
    created_at: datetime


class EvalReportDetail(EvalReportSummary):
    """详情视图：列表字段 + 完整逐 case 结果（API 详情响应 / 仓储回读）。"""

    results: list[CaseResult]
