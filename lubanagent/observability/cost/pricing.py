"""价格读取与用量计费：价格来自 ModelDefinition.params，缺价记 None 而非 0。"""

from lubanagent.core import ModelDefinition, TokenUsage

INPUT_PRICE_KEY = "input_price_per_1k"
OUTPUT_PRICE_KEY = "output_price_per_1k"


def prices_of(definition: ModelDefinition) -> dict[str, float] | None:
    """从 params 读取元/1k tokens 价格；两个键齐全才返回 {"input", "output"}，缺任一为 None。"""
    input_price = definition.params.get(INPUT_PRICE_KEY)
    output_price = definition.params.get(OUTPUT_PRICE_KEY)
    if input_price is None or output_price is None:
        return None
    return {"input": float(input_price), "output": float(output_price)}


def cost_for_usage(prices: dict[str, float] | None, usage: TokenUsage) -> float | None:
    """按 元/1k tokens 计价；prices 为 None（未知价格）时返回 None，不记 0。"""
    if prices is None:
        return None
    return usage.input_tokens / 1000 * prices["input"] + usage.output_tokens / 1000 * prices[
        "output"
    ]
