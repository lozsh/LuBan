"""evals 路由：评测报告列表与详情（R5 评测体系打通，读真实 eval_results）。

报告由 `luban eval run` 落库；API 只读展示，不承担评测执行
（评测入口在 CLI，回归门禁语义见契约 D2-1 决策 4）。
"""

from fastapi import APIRouter, Query

from lubanagent.api.deps import CtxDep
from lubanagent.core import ErrorCode, EvalReportDetail, EvalReportSummary, LubanError
from lubanagent.persistence import EvalRepository

router = APIRouter(tags=["evals"])


@router.get("/evals", response_model=list[EvalReportSummary])
async def list_evals(
    ctx: CtxDep,
    dataset_key: str | None = None,
    agent_key: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[EvalReportSummary]:
    """报告摘要列表（倒序）；dataset_key/agent_key 可选过滤。"""
    async with ctx.db.session() as session:
        return await EvalRepository(session).list_summaries(
            dataset_key=dataset_key, agent_key=agent_key, limit=limit
        )


@router.get("/evals/{result_id}", response_model=EvalReportDetail)
async def get_eval(result_id: int, ctx: CtxDep) -> EvalReportDetail:
    """报告详情：摘要字段 + 逐 case 结果（pass/fail、断言明细、judge 结论、run_id）。"""
    async with ctx.db.session() as session:
        detail = await EvalRepository(session).get_detail(result_id)
    if detail is None:
        raise LubanError(
            ErrorCode.EVAL_RESULT_NOT_FOUND,
            f"评测报告不存在: {result_id}",
            details={"result_id": result_id},
        )
    return detail
