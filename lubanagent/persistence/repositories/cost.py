"""成本仓储：token_usages/cost_records 的聚合查询，供成本报表（D2-2）消费。"""

from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.persistence.models import AgentRow, CostRecordRow, RunRow, TokenUsageRow


class CostRepository:
    """聚合查询只读仓储；返回 dict 行（键名自文档化），不暴露 ORM 行。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def aggregate_by_agent(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        """join runs+token_usages：每 agent_key 的 runs 数与 input/output tokens 合计。"""
        stmt = (
            select(
                AgentRow.key.label("agent_key"),
                sa.func.count(sa.distinct(RunRow.id)).label("runs"),
                sa.func.coalesce(sa.func.sum(TokenUsageRow.input_tokens), 0).label(
                    "input_tokens"
                ),
                sa.func.coalesce(sa.func.sum(TokenUsageRow.output_tokens), 0).label(
                    "output_tokens"
                ),
            )
            .select_from(TokenUsageRow)
            .join(RunRow, TokenUsageRow.run_id == RunRow.id)
            .join(AgentRow, RunRow.agent_id == AgentRow.id)
            .group_by(AgentRow.key)
            .order_by(AgentRow.key)
        )
        if since is not None:
            stmt = stmt.where(TokenUsageRow.created_at >= since)
        rows = (await self._session.execute(stmt)).all()

        model_keys = await self._model_keys_by_agent()
        return [
            {
                "agent_key": row.agent_key,
                "runs": int(row.runs),
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "model_keys": model_keys.get(row.agent_key, []),
            }
            for row in rows
        ]

    async def aggregate_by_session(self, agent_key: str | None = None) -> list[dict[str, Any]]:
        """按 session_id 聚合 runs 数与 token 合计（仅统计有会话的 Run）。"""
        stmt = (
            select(
                RunRow.session_id.label("session_id"),
                sa.func.count(sa.distinct(RunRow.id)).label("runs"),
                sa.func.coalesce(sa.func.sum(TokenUsageRow.input_tokens), 0).label(
                    "input_tokens"
                ),
                sa.func.coalesce(sa.func.sum(TokenUsageRow.output_tokens), 0).label(
                    "output_tokens"
                ),
            )
            .select_from(TokenUsageRow)
            .join(RunRow, TokenUsageRow.run_id == RunRow.id)
            .where(RunRow.session_id.isnot(None))
            .group_by(RunRow.session_id)
            .order_by(RunRow.session_id)
        )
        if agent_key is not None:
            stmt = stmt.join(AgentRow, RunRow.agent_id == AgentRow.id).where(
                AgentRow.key == agent_key
            )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "session_id": row.session_id,
                "runs": int(row.runs),
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
            }
            for row in rows
        ]

    async def aggregate_by_day(self, *, days: int = 7) -> list[dict[str, Any]]:
        """最近 days 天按 date(created_at) 分组（sa.func.date 统一 SQLite/MySQL 方言）。"""
        day = sa.func.date(TokenUsageRow.created_at)
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(
                day.label("day"),
                sa.func.count(sa.distinct(RunRow.id)).label("runs"),
                sa.func.coalesce(sa.func.sum(TokenUsageRow.input_tokens), 0).label(
                    "input_tokens"
                ),
                sa.func.coalesce(sa.func.sum(TokenUsageRow.output_tokens), 0).label(
                    "output_tokens"
                ),
            )
            .select_from(TokenUsageRow)
            .join(RunRow, TokenUsageRow.run_id == RunRow.id)
            .where(TokenUsageRow.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "day": str(row.day),
                "runs": int(row.runs),
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
            }
            for row in rows
        ]

    async def cost_rows(self, *, agent_key: str | None = None) -> list[dict[str, Any]]:
        """cost_records join runs：每 model_key 的 cost 合计（元）。"""
        stmt = (
            select(
                CostRecordRow.model_key.label("model_key"),
                sa.func.count(sa.distinct(CostRecordRow.run_id)).label("runs"),
                sa.func.coalesce(sa.func.sum(CostRecordRow.cost), 0.0).label("cost"),
            )
            .select_from(CostRecordRow)
            .join(RunRow, CostRecordRow.run_id == RunRow.id)
            .group_by(CostRecordRow.model_key)
            .order_by(CostRecordRow.model_key)
        )
        if agent_key is not None:
            stmt = stmt.join(AgentRow, RunRow.agent_id == AgentRow.id).where(
                AgentRow.key == agent_key
            )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "model_key": row.model_key,
                "runs": int(row.runs),
                "cost": float(row.cost),
            }
            for row in rows
        ]

    async def _model_keys_by_agent(self) -> dict[str, list[str]]:
        """每 agent 实际调用过的模型键集合（供报表/建议定位高价模型）。"""
        stmt = (
            select(AgentRow.key, TokenUsageRow.model_key)
            .select_from(TokenUsageRow)
            .join(RunRow, TokenUsageRow.run_id == RunRow.id)
            .join(AgentRow, RunRow.agent_id == AgentRow.id)
            .distinct()
        )
        pairs = (await self._session.execute(stmt)).all()
        result: dict[str, list[str]] = {}
        for agent_key, model_key in pairs:
            result.setdefault(agent_key, []).append(model_key)
        return {key: sorted(keys) for key, keys in result.items()}

    async def run_tree_cost(self, run_id: str) -> dict[str, Any] | None:
        """Run 树成本归因（H11 报表口径）：本 Run 自身 vs 全部后代的费用，明示分开。

        每个 Run 的 cost_records 只记自己模型的消费（本地归因），故 total = own +
        descendants 不重复相加。Run 不存在 -> None；BFS 逐层查后代（parent_run_id），
        每层一次查询，不依赖方言递归 CTE。
        """
        root = await self._session.get(RunRow, run_id)
        if root is None:
            return None
        own_cost = await self._sum_cost(run_id)
        descendant_ids: list[str] = []
        frontier = [run_id]
        seen: set[str] = {run_id}
        while frontier:
            rows = (
                await self._session.scalars(
                    select(RunRow.id).where(RunRow.parent_run_id.in_(frontier))
                )
            ).all()
            frontier = [rid for rid in rows if rid not in seen]
            for rid in frontier:
                seen.add(rid)
            descendant_ids.extend(frontier)
        descendants_cost = 0.0
        for rid in descendant_ids:
            descendants_cost += await self._sum_cost(rid)
        return {
            "run_id": run_id,
            "own_cost": own_cost,
            "descendants_cost": descendants_cost,
            "total_cost": own_cost + descendants_cost,
            "own_runs": 1,
            "descendant_runs": len(descendant_ids),
        }

    async def _sum_cost(self, run_id: str) -> float:
        stmt = (
            select(sa.func.coalesce(sa.func.sum(CostRecordRow.cost), 0.0)).where(
                CostRecordRow.run_id == run_id
            )
        )
        return float((await self._session.scalar(stmt)) or 0.0)
