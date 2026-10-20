"""advice 单元测试：三条规则各命中/不命中，无命中返回空列表。"""

from lubanagent.observability.cost import CostReport, cost_advice


def agent_row(
    key: str, runs: int, input_tokens: int, output_tokens: int, model_keys: list[str]
) -> dict:
    return {
        "agent_key": key,
        "runs": runs,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model_keys": model_keys,
    }


def model_row(model_key: str, output_price: float | None) -> dict:
    return {
        "model_key": model_key,
        "runs": 1,
        "cost": 0.1,
        "input_price_per_1k": output_price,
        "output_price_per_1k": output_price,
    }


def test_rule1_input_ratio_hit() -> None:
    report = CostReport(by_agent=[agent_row("a", 1, 1000, 50, [])])
    advice = cost_advice(report)
    assert len(advice) == 1
    assert "a 输入占比过高" in advice[0]
    assert "摘要记忆" in advice[0]


def test_rule1_input_ratio_not_hit() -> None:
    report = CostReport(by_agent=[agent_row("a", 1, 100, 50, [])])  # 2:1
    assert cost_advice(report) == []


def test_rule1_zero_output_with_input_is_hit() -> None:
    report = CostReport(by_agent=[agent_row("a", 1, 100, 0, [])])
    assert any("输入占比过高" in item for item in cost_advice(report))


def test_rule2_high_volume_high_price_hit() -> None:
    report = CostReport(
        by_agent=[agent_row("hot", 300, 100, 50, ["expensive"])],
        by_day=[{"day": "2026-07-22"}, {"day": "2026-07-23"}],  # 2 天 → 日均 150
        cost_by_model=[model_row("cheap", 0.1), model_row("expensive", 2.0)],
    )
    advice = cost_advice(report)
    assert any("hot" in item and "cheap 槽位" in item for item in advice)


def test_rule2_low_volume_not_hit() -> None:
    report = CostReport(
        by_agent=[agent_row("cold", 100, 100, 50, ["expensive"])],
        by_day=[{"day": "2026-07-22"}, {"day": "2026-07-23"}],  # 日均 50
        cost_by_model=[model_row("cheap", 0.1), model_row("expensive", 2.0)],
    )
    assert cost_advice(report) == []


def test_rule2_cheapest_model_not_hit() -> None:
    report = CostReport(
        by_agent=[agent_row("hot", 300, 100, 50, ["cheap"])],
        by_day=[{"day": "2026-07-22"}],
        cost_by_model=[model_row("cheap", 0.1), model_row("expensive", 2.0)],
    )
    assert cost_advice(report) == []


def test_rule3_unknown_pricing_hit() -> None:
    report = CostReport(unknown_pricing_models=["ghost-model"])
    advice = cost_advice(report)
    assert advice == [
        "为模型 ghost-model 补 params.input_price_per_1k/output_price_per_1k 以启用成本核算"
    ]


def test_no_hit_returns_empty() -> None:
    report = CostReport(
        by_agent=[agent_row("a", 10, 100, 50, ["cheap"])],
        by_day=[{"day": "2026-07-23"}],
        cost_by_model=[model_row("cheap", 0.1)],
        total_cost=0.1,
    )
    assert cost_advice(report) == []
