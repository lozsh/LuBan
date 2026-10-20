"""事前 token/成本估算：chars/4 启发式 + 工具 schema 开销常数，近似值非精确（PRD F7）。"""

import math

from lubanagent.core import AgentDefinition, ModelDefinition, TokenEstimate, TokenUsage

from .pricing import cost_for_usage, prices_of

CHARS_PER_TOKEN: int = 4
TOOL_SCHEMA_OVERHEAD_TOKENS: int = 200  # 每个绑定工具的 schema 近似开销
ESTIMATED_OUTPUT_TOKENS: int = 512  # 输出固定近似值，与 ModelCapability.max_output 无关


def _render_instructions(definition: AgentDefinition) -> str:
    """用 prompt_variables 做 {name} 占位替换（非 format，未知占位原样保留）。"""
    rendered = definition.instructions
    for key, value in definition.prompt_variables.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def estimate_tokens(
    definition: AgentDefinition, user_input: str, *, history_chars: int = 0
) -> TokenEstimate:
    """调用前 token 估算（纯计算无 IO）。

    input = (渲染后 instructions + user_input + history_chars) / CHARS_PER_TOKEN（上取整）
            + 绑定工具数 * TOOL_SCHEMA_OVERHEAD_TOKENS
    output = ESTIMATED_OUTPUT_TOKENS（固定近似值）
    """
    total_chars = len(_render_instructions(definition)) + len(user_input) + history_chars
    input_tokens = (
        math.ceil(total_chars / CHARS_PER_TOKEN)
        + len(definition.tools) * TOOL_SCHEMA_OVERHEAD_TOKENS
    )
    return TokenEstimate(input_tokens=input_tokens, output_tokens=ESTIMATED_OUTPUT_TOKENS)


def estimate_cost(
    definition: AgentDefinition,
    model: ModelDefinition,
    user_input: str,
    *,
    history_chars: int = 0,
) -> float | None:
    """估算单次 Run 近似成本（元）；模型无价格信息时返回 None。"""
    estimate = estimate_tokens(definition, user_input, history_chars=history_chars)
    usage = TokenUsage(
        input_tokens=estimate.input_tokens, output_tokens=estimate.output_tokens
    )
    return cost_for_usage(prices_of(model), usage)
