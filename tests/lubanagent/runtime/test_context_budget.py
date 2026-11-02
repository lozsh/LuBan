"""context_budget 纯函数验收：plan_inline 分配 / 巨型条目截断 / cite 与 plain 格式化。"""

from lubanagent.runtime.context_budget import (
    InlineItem,
    format_knowledge_block,
    format_memory_block,
    make_item,
    plan_inline,
)


def _item(key: str, text: str) -> InlineItem:
    return make_item(key, text, budget=None)


# ------------------------------------------------------------ plan_inline


def test_plan_inline_budget_none_all_inline():
    """budget=None → 全部 inline（旧行为通道）。"""
    items = [_item("kn:1", "a" * 500), _item("kn:2", "b" * 500)]
    plan = plan_inline(items, None)
    assert plan.inline == items
    assert plan.spilled == []


def test_plan_inline_accumulates_until_budget():
    """逐条累加：放得下内联，放不下整条 spill（不截半条）。"""
    items = [_item("kn:1", "a" * 30), _item("kn:2", "b" * 30), _item("kn:3", "c" * 30)]
    plan = plan_inline(items, 50)
    assert [it.key for it in plan.inline] == ["kn:1"]
    assert [it.key for it in plan.spilled] == ["kn:2", "kn:3"]


def test_plan_inline_empty():
    plan = plan_inline([], 100)
    assert plan.inline == []
    assert plan.spilled == []


# ------------------------------------------------------------ make_item / preview


def test_make_item_normal_preview_first_120_chars():
    text = "x" * 300
    item = make_item("kn:1", text, budget=1000)
    assert len(item.preview) == 120
    assert item.text == text


def test_make_item_giant_head_tail_truncation():
    """单独就超预算的巨型条目：preview 用头 60% + 尾 20% 截断。"""
    text = "H" * 1000 + "T" * 100
    item = make_item("kn:1", text, budget=100)
    assert item.preview.startswith("H" * 60)
    assert item.preview.endswith("T" * 20)
    assert "……" in item.preview


def test_make_item_preview_single_line():
    item = make_item("mem:1", "第一行\n第二行", budget=None)
    assert "\n" not in item.preview


# ------------------------------------------------------------ format_knowledge_block


def test_format_knowledge_cite_inline_and_spill():
    items = [_item("kn:1", "全文甲"), _item("kn:2", "全文乙")]
    plan = plan_inline(items, 5)
    block = format_knowledge_block(items, plan, cite=True)
    assert "[1] 全文甲〔ref: kn:1〕" in block
    assert "[2] (未内联)" in block and "kn:2" in block and "knowledge_read" in block
    assert block.count("knowledge_read") == 2  # 引用行 + 末尾使用说明


def test_format_knowledge_plain_mode():
    items = [_item("kn:1", "全文甲"), _item("kn:2", "全文乙")]
    plan = plan_inline(items, 5)
    block = format_knowledge_block(items, plan, cite=False)
    assert "[1]" not in block.split("(未内联)")[0]  # 内联条目无编号
    assert "全文甲" in block
    assert "kn:2" in block


def test_format_knowledge_no_spill_no_usage_note():
    items = [_item("kn:1", "短文")]
    plan = plan_inline(items, 100)
    block = format_knowledge_block(items, plan, cite=True)
    assert "部分片段仅有摘要" not in block
    assert "[1] 短文〔ref: kn:1〕" in block


def test_format_knowledge_custom_read_tool_key():
    items = [_item("kn:1", "全文甲")]
    plan = plan_inline(items, 1)
    block = format_knowledge_block(items, plan, cite=True, read_tool_key="luban:knowledge_read")
    assert "luban:knowledge_read" in block


# ------------------------------------------------------------ format_memory_block


def test_format_memory_block_inline_and_spill():
    items = [_item("mem:1", "喜欢喝美式"), _item("mem:2", "在杭州工作")]
    plan = plan_inline(items, 5)
    block = format_memory_block(items, plan)
    assert block.startswith("已知用户信息：")
    assert "- 喜欢喝美式" in block
    assert "- (摘要)" in block and "mem:2" in block and "memory_read" in block


def test_format_memory_block_empty():
    assert format_memory_block([], plan_inline([], None)) == ""
