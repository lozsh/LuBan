"""评测报告：单数据集结果 + 两版对比（回归门禁的数据源，契约 D2-1 决策 4）。

领域模型已下沉 `lubanagent.core.evaluation`（persistence 仓储需要领域对象承载，
分层契约禁止 persistence → observability）；本模块保留原路径 re-export，
既有导入（含 CLI/测试）零改动。
"""

from lubanagent.core.evaluation import (
    CaseResult,
    ComparisonReport,
    EvalReport,
    compare_reports,
)

__all__ = ["CaseResult", "ComparisonReport", "EvalReport", "compare_reports"]
