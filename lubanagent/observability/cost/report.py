"""事后成本报表：按 Agent/天/模型归集真实 DB 记录，未知价格显式列名而非记 0。"""

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import ErrorCode, LubanError, ModelDefinition
from lubanagent.persistence.models import ModelRow
from lubanagent.persistence.repositories.cost import CostRepository

from .pricing import prices_of


class CostReport(BaseModel):
    """成本报表：聚合行 + 总成本；存在未知价格模型时 total_cost 为 None。"""

    by_agent: list[dict[str, Any]] = Field(default_factory=list)
    by_day: list[dict[str, Any]] = Field(default_factory=list)
    cost_by_model: list[dict[str, Any]] = Field(default_factory=list)
    total_cost: float | None = None
    unknown_pricing_models: list[str] = Field(default_factory=list)


class RunTreeCostReport(BaseModel):
    """Run 树成本归因（H11 明示口径）：父自身与后代分开列，total = own + descendants。

    每个 Run 的 cost_records 只记自己模型的消费（本地归因），聚合不重复相加。
    """

    run_id: str
    own_cost: float = 0.0
    descendants_cost: float = 0.0
    total_cost: float = 0.0
    own_runs: int = 0
    descendant_runs: int = 0


async def build_cost_report(session: AsyncSession, *, days: int = 7) -> CostReport:
    """从 DB 真实记录构建成本报表（观测数据驱动，与 evals 同口径）。"""
    repo = CostRepository(session)
    by_agent = await repo.aggregate_by_agent()
    by_day = await repo.aggregate_by_day(days=days)
    cost_by_model = await repo.cost_rows()

    model_keys = sorted(
        {row["model_key"] for row in cost_by_model}
        | {key for row in by_agent for key in row["model_keys"]}
    )
    prices_map = await _load_prices(session, model_keys)

    for row in cost_by_model:
        prices = prices_map.get(row["model_key"])
        row["input_price_per_1k"] = prices["input"] if prices else None
        row["output_price_per_1k"] = prices["output"] if prices else None

    unknown = [key for key in model_keys if prices_map.get(key) is None]
    total_cost = (
        None if unknown else sum(row["cost"] for row in cost_by_model)
    )
    return CostReport(
        by_agent=by_agent,
        by_day=by_day,
        cost_by_model=cost_by_model,
        total_cost=total_cost,
        unknown_pricing_models=unknown,
    )


async def build_run_tree_cost_report(session: AsyncSession, run_id: str) -> RunTreeCostReport:
    """按 parent_run_id 逐层归集一棵 Run 树的费用：own 与后代明示分开，不重复相加。"""
    row = await CostRepository(session).run_tree_cost(run_id)
    if row is None:
        raise LubanError(
            ErrorCode.RUN_NOT_FOUND, f"Run 不存在: {run_id}", details={"run_id": run_id}
        )
    return RunTreeCostReport(**row)


async def _load_prices(
    session: AsyncSession, model_keys: list[str]
) -> dict[str, dict[str, float] | None]:
    """读取模型注册记录的 params 价格；未注册或缺价均为 None（未知价格）。"""
    if not model_keys:
        return {}
    rows = (
        await session.scalars(select(ModelRow).where(ModelRow.key.in_(model_keys)))
    ).all()
    registered = {row.key: row for row in rows}
    result: dict[str, dict[str, float] | None] = {}
    for key in model_keys:
        row = registered.get(key)
        if row is None:
            result[key] = None
            continue
        definition = ModelDefinition(
            key=row.key,
            provider=row.provider,
            model_name=row.model_name,
            factory=row.factory,
            params=row.params,
        )
        result[key] = prices_of(definition)
    return result
