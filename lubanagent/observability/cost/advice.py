"""成本优化建议：规则制诚实最小集，命中才给建议，无命中返回空列表。"""

from .report import CostReport

INPUT_OUTPUT_RATIO_THRESHOLD = 10  # 输入/输出 token 占比阈值
DAILY_RUNS_THRESHOLD = 100  # 单 agent 日均 runs 阈值


def cost_advice(report: CostReport) -> list[str]:
    """按固定规则从 CostReport 生成建议（纯函数无 IO）。

    规则：
    1. 某 agent input_tokens/output_tokens > 10 → 检查上下文膨胀；
    2. 单 agent 日均 runs > 100 且用了高价模型 → 评估 cheap 槽位
       （"高价"= 输出价格已知且高于报表内最低已知输出价格，即存在更便宜的已知替代）；
    3. unknown_pricing_models 非空 → 提示补价格参数。
    """
    advice: list[str] = []
    days = max(len(report.by_day), 1)
    high_price_models = _high_price_models(report)

    for row in report.by_agent:
        input_tokens = row["input_tokens"]
        output_tokens = row["output_tokens"]
        ratio = input_tokens / output_tokens if output_tokens > 0 else float("inf")
        if input_tokens > 0 and ratio > INPUT_OUTPUT_RATIO_THRESHOLD:
            advice.append(
                f"{row['agent_key']} 输入占比过高（>10:1）："
                "检查上下文是否膨胀，可启用摘要记忆或收紧窗口"
            )
        daily_runs = row["runs"] / days
        used_high = [key for key in row["model_keys"] if key in high_price_models]
        if daily_runs > DAILY_RUNS_THRESHOLD and used_high:
            advice.append(
                f"{row['agent_key']} 日均运行 {daily_runs:.0f} 次（>100）且使用高价模型 "
                f"{', '.join(used_high)}：评估改用 cheap 槽位模型以降低成本"
            )

    for key in report.unknown_pricing_models:
        advice.append(
            f"为模型 {key} 补 params.input_price_per_1k/output_price_per_1k 以启用成本核算"
        )
    return advice


def _high_price_models(report: CostReport) -> set[str]:
    """输出价格已知且高于最低已知输出价格的模型键集合。"""
    known = [
        row["output_price_per_1k"]
        for row in report.cost_by_model
        if row.get("output_price_per_1k") is not None
    ]
    if not known:
        return set()
    cheapest = min(known)
    return {
        row["model_key"]
        for row in report.cost_by_model
        if row.get("output_price_per_1k") is not None
        and row["output_price_per_1k"] > cheapest
    }
