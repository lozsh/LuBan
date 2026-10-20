"""pricing 单元测试：有/无价格两态与 cost_for_usage 数值断言。"""

import pytest

from lubanagent.core import ModelDefinition, TokenUsage
from lubanagent.observability.cost import cost_for_usage, prices_of


def make_model(**params) -> ModelDefinition:
    return ModelDefinition(
        key="m", provider="p", model_name="name", factory="f", params=params
    )


def test_prices_of_with_both_keys() -> None:
    prices = prices_of(
        make_model(input_price_per_1k=0.001, output_price_per_1k=0.002)
    )
    assert prices == {"input": 0.001, "output": 0.002}


def test_prices_of_missing_any_key_returns_none() -> None:
    assert prices_of(make_model()) is None
    assert prices_of(make_model(input_price_per_1k=0.001)) is None
    assert prices_of(make_model(output_price_per_1k=0.002)) is None


def test_cost_for_usage_with_prices() -> None:
    prices = {"input": 1.0, "output": 2.0}  # 元 / 1k tokens
    usage = TokenUsage(input_tokens=500, output_tokens=250, model_calls=1)
    # 500/1000*1.0 + 250/1000*2.0 = 0.5 + 0.5
    assert cost_for_usage(prices, usage) == pytest.approx(1.0)


def test_cost_for_usage_without_prices_returns_none() -> None:
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    assert cost_for_usage(None, usage) is None
