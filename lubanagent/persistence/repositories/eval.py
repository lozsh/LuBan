"""评测仓储：数据集与报告的领域对象持久化（D2-1 兑现 + R5 评测体系打通）。

报告走 SkillRepository 同款全量 JSON 模式：report 列存完整 EvalReport 序列化
（逐 case pass/fail、断言明细、judge 结论、run_id/output/error），
另加聚合列（pass_rate/passed/total/错答率）供列表页免解析查询。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    ErrorCode,
    EvalDataset,
    EvalReport,
    EvalReportDetail,
    EvalReportSummary,
    LubanError,
)
from lubanagent.persistence.models import EvalDatasetRow, EvalResultRow


def _summary_of(row: EvalResultRow, dataset_key: str) -> EvalReportSummary:
    """行 → 列表视图（聚合列 + 定位键；不解析全量报告 JSON）。"""
    return EvalReportSummary(
        id=row.id,
        dataset_key=dataset_key,
        agent_key=row.agent_key,
        agent_version=row.agent_version,
        model_key=row.model_key,
        pass_rate=row.pass_rate,
        passed_cases=row.passed_cases,
        total_cases=row.total_cases,
        wrong_answer_rate=row.wrong_answer_rate,
        created_at=row.created_at,
    )


class EvalRepository:
    """eval_datasets / eval_results 的领域对象读写。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_dataset(self, dataset: EvalDataset, *, version: str = "1") -> None:
        """登记数据集（key 唯一；调用方先查重，重复插入由唯一约束兜底）。"""
        self._session.add(
            EvalDatasetRow(
                key=dataset.key,
                name=dataset.name,
                version=version,
                cases=[case.model_dump(mode="json") for case in dataset.cases],
            )
        )
        await self._session.flush()

    async def get_dataset(self, key: str) -> EvalDataset | None:
        row = await self._session.scalar(
            select(EvalDatasetRow).where(EvalDatasetRow.key == key)
        )
        if row is None:
            return None
        return EvalDataset.model_validate({"key": row.key, "name": row.name, "cases": row.cases})

    async def save_report(self, report: EvalReport) -> int:
        """完整报告落库（数据集须已登记）；返回结果行 id。"""
        dataset = await self._session.scalar(
            select(EvalDatasetRow).where(EvalDatasetRow.key == report.dataset_key)
        )
        if dataset is None:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR, f"评测数据集不存在: {report.dataset_key}"
            )
        row = EvalResultRow(
            dataset_id=dataset.id,
            agent_key=report.agent_key,
            agent_version=report.agent_version,
            model_key=report.model_key,
            pass_rate=report.pass_rate,
            passed_cases=sum(1 for result in report.results if result.passed),
            total_cases=len(report.results),
            wrong_answer_rate=report.wrong_answer_rate,
            report=report.model_dump(mode="json"),
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def get_detail(self, result_id: int) -> EvalReportDetail | None:
        """结果行 → 详情视图（聚合列 + 全量逐 case 结果）。"""
        row_pair = (
            await self._session.execute(
                select(EvalResultRow, EvalDatasetRow.key)
                .join(EvalDatasetRow, EvalResultRow.dataset_id == EvalDatasetRow.id)
                .where(EvalResultRow.id == result_id)
            )
        ).first()
        if row_pair is None:
            return None
        row, dataset_key = row_pair
        return EvalReportDetail(
            **_summary_of(row, dataset_key).model_dump(),
            results=EvalReport.model_validate(row.report).results,
        )

    async def list_summaries(
        self,
        *,
        dataset_key: str | None = None,
        agent_key: str | None = None,
        limit: int = 50,
    ) -> list[EvalReportSummary]:
        """按时间倒序列报告摘要（列表页）；dataset_key/agent_key 可选过滤。"""
        stmt = (
            select(EvalResultRow, EvalDatasetRow.key)
            .join(EvalDatasetRow, EvalResultRow.dataset_id == EvalDatasetRow.id)
            .order_by(EvalResultRow.id.desc())
            .limit(limit)
        )
        if dataset_key is not None:
            stmt = stmt.where(EvalDatasetRow.key == dataset_key)
        if agent_key is not None:
            stmt = stmt.where(EvalResultRow.agent_key == agent_key)
        rows = (await self._session.execute(stmt)).all()
        return [_summary_of(row, dataset_key_) for row, dataset_key_ in rows]
