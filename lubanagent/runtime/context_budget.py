"""上下文注入预算：Inline & Spill 规划与格式化（纯函数，不触 DB/网络）。

预算内全文内联（Inline），超预算条目整条降级为引用行（Spill），模型用
knowledge_read / memory_read 伴生工具按需取全文（链式召回的最小落法）。
budget=None 时调用方直接走原路径（本模块不参与），保证存量行为逐字符不变。
"""

from dataclasses import dataclass, field

_PREVIEW_CHARS = 120  # 常规条目摘要长度（去换行后）
_GIANT_HEAD_RATIO = 0.6  # 巨型条目（单独就超预算）摘要取头部比例
_GIANT_TAIL_RATIO = 0.2  # 巨型条目摘要取尾部比例


@dataclass(frozen=True)
class InlineItem:
    """一条可注入内容；key 为引用 id（"kn:12" / "mem:3"），preview 为单行摘要。"""

    key: str
    text: str
    preview: str = ""


@dataclass(frozen=True)
class InlinePlan:
    """规划结果：预算内全文内联 + 超预算只留引用行。"""

    inline: list[InlineItem] = field(default_factory=list)
    spilled: list[InlineItem] = field(default_factory=list)


def make_item(key: str, text: str, *, budget: int | None) -> InlineItem:
    """构造 InlineItem 并按预算生成 preview；巨型条目（单独就超预算）用头+尾截断。"""
    flat = " ".join(text.split())
    if budget is not None and len(flat) > budget:
        head = max(1, int(budget * _GIANT_HEAD_RATIO))
        tail = max(1, int(budget * _GIANT_TAIL_RATIO))
        preview = f"{flat[:head]}……{flat[-tail:]}"
    else:
        preview = flat[:_PREVIEW_CHARS]
    return InlineItem(key=key, text=text, preview=preview)


def plan_inline(items: list[InlineItem], budget: int | None) -> InlinePlan:
    """逐条累加分配预算：放得下内联全文，放不下整条 spill（不截半条，防幻觉）。

    budget=None → 全部 inline（旧行为）；空列表 → 空 plan。
    """
    if budget is None:
        return InlinePlan(inline=list(items), spilled=[])
    inline: list[InlineItem] = []
    spilled: list[InlineItem] = []
    used = 0
    for item in items:
        if used + len(item.text) <= budget:
            inline.append(item)
            used += len(item.text)
        else:
            spilled.append(item)
    return InlinePlan(inline=inline, spilled=spilled)


def format_knowledge_block(
    items: list[InlineItem],
    plan: InlinePlan,
    *,
    cite: bool,
    read_tool_key: str = "knowledge_read",
) -> str:
    """格式化知识片段块；spilled 条目输出引用行 + 末尾使用说明（仅存在 spilled 时）。

    cite 模式：[n] content〔ref: kn:12〕（保持 [n] 编号引用约束）；
    plain 模式：内联条目纯文本拼接，spilled 条目仍出引用行（不可读回的内容不能悄悄丢）。
    """
    numbered = {id(item): index for index, item in enumerate(items, start=1)}
    lines: list[str] = []
    for item in items:
        n = numbered[id(item)]
        if item in plan.inline:
            if cite:
                lines.append(f"[{n}] {item.text}〔ref: {item.key}〕")
            else:
                lines.append(item.text)
        else:
            if cite:
                lines.append(
                    f"[{n}] (未内联) {item.preview}…〔ref: {item.key}，"
                    f"用 {read_tool_key} 获取全文〕"
                )
            else:
                lines.append(
                    f"(未内联) {item.preview}…〔ref: {item.key}，用 {read_tool_key} 获取全文〕"
                )
    if plan.spilled:
        lines.append(
            f"（部分片段仅有摘要，需要全文时用 {read_tool_key} 工具按 ref 取回）"
        )
    return "\n".join(lines)


def format_memory_block(
    items: list[InlineItem], plan: InlinePlan, *, read_tool_key: str = "memory_read"
) -> str:
    """格式化长期记忆块；超预算条目输出摘要引用行 + 末尾使用说明。空 plan → ""。"""
    if not items:
        return ""
    lines = []
    for item in items:
        if item in plan.inline:
            lines.append(f"- {item.text}")
        else:
            lines.append(
                f"- (摘要) {item.preview}…〔ref: {item.key}，用 {read_tool_key} 获取全文〕"
            )
    if plan.spilled:
        lines.append(f"（部分信息仅有摘要，需要全文时用 {read_tool_key} 工具按 ref 取回）")
    return "已知用户信息：\n" + "\n".join(lines)


__all__ = [
    "InlineItem",
    "InlinePlan",
    "format_knowledge_block",
    "format_memory_block",
    "make_item",
    "plan_inline",
]
