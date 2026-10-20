"""上下文组装单元测试：SafeDict 变量渲染与历史窗口截尾。"""

from langchain_core.messages import AIMessage, HumanMessage

from lubanagent.core import AgentDefinition
from lubanagent.runtime import ContextBuilder


def test_system_prompt_merges_variables_and_keeps_missing_placeholders() -> None:
    definition = AgentDefinition(
        key="a",
        name="a",
        role="你是{domain}客服。",
        instructions="语气{tone}，公司：{company}。未提供的{missing}保留。",
        prompt_variables={"tone": "亲切", "company": "Luban"},
    )
    prompt = ContextBuilder().build_system_prompt(definition, variables={"domain": "电商"})
    assert prompt == "你是电商客服。\n\n语气亲切，公司：Luban。未提供的{missing}保留。"


def test_system_prompt_empty_when_no_role_and_instructions() -> None:
    definition = AgentDefinition(key="a", name="a")
    assert ContextBuilder().build_system_prompt(definition) == ""


def test_build_messages_truncates_oldest_and_appends_user_input() -> None:
    history = [HumanMessage(content=f"m{i}") for i in range(5)]
    messages = ContextBuilder().build_messages(
        history=history, user_input="最新问题", window_size=2
    )
    assert [m.content for m in messages] == ["m3", "m4", "最新问题"]
    assert isinstance(messages[-1], HumanMessage)
    assert isinstance(messages[0], HumanMessage)


def test_build_messages_none_history() -> None:
    messages = ContextBuilder().build_messages(history=None, user_input="hi", window_size=20)
    assert len(messages) == 1
    assert messages[0].content == "hi"


def test_build_messages_preserves_message_types() -> None:
    history = [HumanMessage(content="q"), AIMessage(content="a")]
    messages = ContextBuilder().build_messages(history=history, user_input="q2", window_size=10)
    assert isinstance(messages[1], AIMessage)
    assert [m.content for m in messages] == ["q", "a", "q2"]


def test_system_prompt_json_braces_survive_without_variables() -> None:
    """指令含 JSON 字面量（supervisor 输出格式样例）且无变量时，原文透传不炸。

    修复前回归：str.format_map 把 {"action": "answer"} 当模板字段，
    ValueError: Invalid format specifier 直接打死整个 Run（首发 bug，
    内置 cs-triage 真实指令即含此样例——团队 Run 全挂）。
    """
    definition = AgentDefinition(
        key="triage",
        name="triage",
        instructions='只输出：{"action": "answer", "answer": "<最终答复>"}',
    )
    prompt = ContextBuilder().build_system_prompt(definition)
    assert '{"action": "answer"' in prompt


def test_system_prompt_variables_render_and_json_braces_untouched() -> None:
    """声明变量 + 指令含 JSON 样例：{name} 渲染、JSON 原样、未声明占位符保留。"""
    definition = AgentDefinition(
        key="greeter",
        name="greeter",
        instructions=(
            "你好 {name}，只输出：{\"action\": \"answer\"}，"
            "未知占位符 {unknown_var} 保留"
        ),
        prompt_variables={"name": "小图"},
    )
    prompt = ContextBuilder().build_system_prompt(definition)
    assert prompt.startswith("你好 小图")
    assert '{"action": "answer"}' in prompt
    assert "{unknown_var}" in prompt


def test_system_prompt_runtime_variables_override_definitions() -> None:
    """运行时 variables 覆盖定义内 prompt_variables（合并优先级不变）。"""
    definition = AgentDefinition(
        key="greeter",
        name="greeter",
        instructions="你好 {name}",
        prompt_variables={"name": "定义值"},
    )
    prompt = ContextBuilder().build_system_prompt(definition, variables={"name": "运行时值"})
    assert prompt == "你好 运行时值"
