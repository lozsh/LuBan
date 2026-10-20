"""上下文组装：KV-cache 友好（稳定前缀 + append-only + 动态后置），尽力而为非硬不变量。"""

import re

from langchain_core.messages import BaseMessage, HumanMessage

from lubanagent.core import AgentDefinition

# 变量占位符 = 整个花括号内恰为一个标识符（{name}）。
# 不用 str.format_map：它会把指令里的 JSON 字面量（如 supervisor 输出格式样例
# {"action": "answer", ...}）当模板字段解析，格式规格符直接 ValueError 炸掉
# 整个 Run（首发 bug：内置 cs-triage 的指令即含此样例，真实团队 Run 全挂）。
_VAR_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ContextBuilder:
    """KV-cache 友好组装（技术方案 §13.6）：稳定前缀 + append-only + 动态后置。"""

    def build_system_prompt(
        self, definition: AgentDefinition, *, variables: dict[str, str] | None = None
    ) -> str:
        """role 段 + instructions 段；variables 合并 definition.prompt_variables 后渲染。

        渲染语义：只替换与已声明变量同名的 {name} 占位符；未声明的占位符与
        一切非占位符花括号内容（JSON 样例等）原样保留。
        """
        merged = {**definition.prompt_variables, **(variables or {})}
        sections = [part for part in (definition.role, definition.instructions) if part]
        text = "\n\n".join(sections)
        if not merged:
            return text
        return _VAR_PATTERN.sub(
            lambda match: str(merged.get(match.group(1), match.group(0))), text
        )

    def build_messages(
        self, *, history: list[BaseMessage] | None, user_input: str, window_size: int
    ) -> list[BaseMessage]:
        """history 截尾 window_size 条（保最新，截最旧）+ 追加 HumanMessage；append-only。"""
        messages = list(history or [])
        if window_size > 0:
            messages = messages[-window_size:]
        else:
            messages = []
        messages.append(HumanMessage(content=user_input))
        return messages
