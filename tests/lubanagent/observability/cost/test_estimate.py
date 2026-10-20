"""estimate 单元测试：已知输入长度 → 期望 TokenEstimate（含工具开销项）；无价格 → None。"""

import pytest

from lubanagent.core import AgentDefinition, ModelDefinition
from lubanagent.observability.cost import (
    CHARS_PER_TOKEN,
    TOOL_SCHEMA_OVERHEAD_TOKENS,
    estimate_cost,
    estimate_tokens,
)


def make_agent(instructions: str, tools: list[str] | None = None, **kwargs) -> AgentDefinition:
    return AgentDefinition(
        key="a", name="a", instructions=instructions, tools=tools or [], **kwargs
    )


def make_model(**params) -> ModelDefinition:
    return ModelDefinition(
        key="m", provider="p", model_name="name", factory="f", params=params
    )


def test_estimate_tokens_known_lengths_with_tool_overhead() -> None:
    agent = make_agent("a" * 40, tools=["t1", "t2"])
    estimate = estimate_tokens(agent, "b" * 8)
    # ceil((40+8)/4) = 12，加 2 个工具 * 200 = 412；输出固定 512 近似
    assert estimate.input_tokens == 12 + 2 * TOOL_SCHEMA_OVERHEAD_TOKENS
    assert estimate.output_tokens == 512
    assert estimate.total == 412 + 512


def test_estimate_tokens_history_chars_and_ceil() -> None:
    agent = make_agent("a" * 10)
    estimate = estimate_tokens(agent, "b" * 3, history_chars=4)
    # ceil((10+3+4)/4) = ceil(17/4) = 5
    assert estimate.input_tokens == 5


def test_estimate_tokens_renders_prompt_variables() -> None:
    agent = make_agent("你是{role}助手", prompt_variables={"role": "客服"})
    estimate = estimate_tokens(agent, "")
    # 渲染后 "你是客服助手" 6 字符 → ceil(6/4) = 2
    assert estimate.input_tokens == 2


def test_estimate_tokens_constants_documented() -> None:
    assert CHARS_PER_TOKEN == 4
    assert TOOL_SCHEMA_OVERHEAD_TOKENS == 200


def test_estimate_cost_with_prices() -> None:
    agent = make_agent("a" * 40, tools=["t1"])
    model = make_model(input_price_per_1k=1.0, output_price_per_1k=2.0)
    cost = estimate_cost(agent, model, "b" * 8)
    # input = 12 + 200 = 212 → 0.212；output = 512 → 1.024；合计 1.236
    assert cost == pytest.approx(0.212 + 1.024)


def test_estimate_cost_without_prices_returns_none() -> None:
    agent = make_agent("a" * 40)
    model = make_model()
    assert estimate_cost(agent, model, "b" * 8) is None
