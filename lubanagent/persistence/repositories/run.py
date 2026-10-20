"""Run 仓储：运行记录 CRUD 与 token 用量/费用明细，返回 core 领域对象。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    BrainProfile,
    EndReason,
    ErrorCode,
    LubanError,
    Run,
    RunStatus,
    TokenUsage,
)
from lubanagent.persistence.models import (
    AgentRow,
    AgentVersionRow,
    CostRecordRow,
    RunRow,
    TokenUsageRow,
)


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, run: Run) -> None:
        agent_row = await self._session.scalar(
            select(AgentRow).where(AgentRow.key == run.agent_key)
        )
        if agent_row is None:
            raise LubanError(ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {run.agent_key}")
        version_row = await self._session.scalar(
            select(AgentVersionRow).where(
                AgentVersionRow.agent_id == agent_row.id,
                AgentVersionRow.version == run.agent_version,
            )
        )
        if version_row is None:
            raise LubanError(
                ErrorCode.AGENT_VERSION_NOT_FOUND,
                f"版本不存在: {run.agent_key} v{run.agent_version}",
            )
        self._session.add(
            RunRow(
                id=run.id,
                agent_id=agent_row.id,
                agent_version_id=version_row.id,
                session_id=run.session_id,
                user_id=run.user_id,
                parent_run_id=run.parent_run_id,
                status=run.status.value,
                input=run.input,
                output=run.output,
                brain_profile=run.brain_profile.value,
                model_key=run.model_key,
                end_reason=run.end_reason.value if run.end_reason else None,
                token_usage=run.token_usage.model_dump(mode="json"),
                cost=run.cost,
                error=run.error,
                created_at=run.created_at,
                started_at=run.started_at,
                ended_at=run.ended_at,
            )
        )
        await self._session.flush()

    async def get_run(self, run_id: str) -> Run | None:
        row = await self._session.get(RunRow, run_id)
        if row is None:
            return None
        return await self._to_domain(row)

    async def update_run(self, run: Run) -> None:
        """按 id 覆盖状态/输出/结束原因/用量/费用/error/时间戳。"""
        row = await self._session.get(RunRow, run.id)
        if row is None:
            raise LubanError(ErrorCode.RUN_NOT_FOUND, f"Run 不存在: {run.id}")
        row.status = run.status.value
        row.output = run.output
        row.end_reason = run.end_reason.value if run.end_reason else None
        row.model_key = run.model_key
        row.token_usage = run.token_usage.model_dump(mode="json")
        row.cost = run.cost
        row.error = run.error
        row.started_at = run.started_at
        row.ended_at = run.ended_at
        await self._session.flush()

    async def list_runs(
        self,
        agent_key: str | None = None,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[Run]:
        stmt = (
            select(RunRow)
            .join(AgentRow, RunRow.agent_id == AgentRow.id)
            .order_by(RunRow.created_at.desc(), RunRow.id.desc())
            .limit(limit)
        )
        if agent_key is not None:
            stmt = stmt.where(AgentRow.key == agent_key)
        if session_id is not None:
            stmt = stmt.where(RunRow.session_id == session_id)
        if user_id is not None:
            stmt = stmt.where(RunRow.user_id == user_id)
        rows = (await self._session.scalars(stmt)).all()
        return [await self._to_domain(row) for row in rows]

    async def run_usage(self, run_id: str) -> tuple[int, int, float]:
        """该 run 账本已消费量：(input_tokens 合计, output_tokens 合计, cost 合计)。

        resume 预算恢复用（缺口 #2）：以账本实consum量初始化重建的 BudgetGuard，
        崩溃续跑不重复放水；token NULL（模型未回报）按 0 计入合计。
        """
        token_row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(TokenUsageRow.input_tokens), 0),
                    func.coalesce(func.sum(TokenUsageRow.output_tokens), 0),
                ).where(TokenUsageRow.run_id == run_id)
            )
        ).one()
        cost_total = (
            await self._session.execute(
                select(func.coalesce(func.sum(CostRecordRow.cost), 0.0)).where(
                    CostRecordRow.run_id == run_id
                )
            )
        ).scalar_one()
        return int(token_row[0]), int(token_row[1]), float(cost_total)

    async def add_token_usage(
        self,
        run_id: str,
        step_index: int | None,
        model_key: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """落一条用量明细；token 为 None 表示模型未回报 usage（H10：显式未知存 NULL）。"""
        self._session.add(
            TokenUsageRow(
                run_id=run_id,
                step_index=step_index,
                model_key=model_key,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        await self._session.flush()

    async def add_cost_record(
        self, run_id: str, model_key: str, cost: float, currency: str = "CNY"
    ) -> None:
        self._session.add(
            CostRecordRow(run_id=run_id, model_key=model_key, cost=cost, currency=currency)
        )
        await self._session.flush()

    async def _to_domain(self, row: RunRow) -> Run:
        agent_row = await self._session.get(AgentRow, row.agent_id)
        version_row = await self._session.get(AgentVersionRow, row.agent_version_id)
        return Run(
            id=row.id,
            agent_key=agent_row.key,
            agent_version=version_row.version,
            session_id=row.session_id,
            user_id=row.user_id,
            parent_run_id=row.parent_run_id,
            status=RunStatus(row.status),
            input=row.input,
            output=row.output,
            brain_profile=BrainProfile(row.brain_profile),
            model_key=row.model_key,
            end_reason=EndReason(row.end_reason) if row.end_reason else None,
            token_usage=TokenUsage.model_validate(row.token_usage),
            cost=row.cost,
            error=row.error,
            created_at=row.created_at,
            started_at=row.started_at,
            ended_at=row.ended_at,
        )
