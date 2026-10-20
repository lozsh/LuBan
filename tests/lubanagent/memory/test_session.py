"""SessionMemory：append/history 窗口截断与类型映射、count/clear、跨轮读回。"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from lubanagent.memory import SessionMemory
from lubanagent.persistence import Database


async def test_append_and_history_roundtrip_across_runs(db: Database) -> None:
    memory = SessionMemory(db)
    # run1 写入
    await memory.append("s1", "user", "你好", run_id="run-1")
    await memory.append("s1", "assistant", "你好，有什么可以帮你？", run_id="run-1")
    # run2 读回（跨轮保上下文）
    await memory.append("s1", "user", "查订单", run_id="run-2")

    history = await memory.history("s1", window_size=20)
    assert [type(m) for m in history] == [HumanMessage, AIMessage, HumanMessage]
    assert [m.content for m in history] == ["你好", "你好，有什么可以帮你？", "查订单"]


async def test_history_window_truncates_to_latest(db: Database) -> None:
    memory = SessionMemory(db)
    for i in range(5):
        await memory.append("s1", "user", f"msg-{i}")

    history = await memory.history("s1", window_size=3)
    assert [m.content for m in history] == ["msg-2", "msg-3", "msg-4"]  # 升序


async def test_history_role_mapping(db: Database) -> None:
    memory = SessionMemory(db)
    await memory.append("s1", "system", "sys")
    await memory.append("s1", "user", "u")
    await memory.append("s1", "assistant", "a")
    await memory.append("s1", "tool", "t")

    history = await memory.history("s1", window_size=10)
    assert [type(m) for m in history] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    tool_msg = history[3]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.tool_call_id == ""  # v1 空串占位


async def test_history_isolated_by_session(db: Database) -> None:
    memory = SessionMemory(db)
    await memory.append("s1", "user", "s1-msg")
    await memory.append("s2", "user", "s2-msg")

    assert [m.content for m in await memory.history("s1", window_size=10)] == ["s1-msg"]
    assert [m.content for m in await memory.history("s2", window_size=10)] == ["s2-msg"]


async def test_count_and_clear(db: Database) -> None:
    memory = SessionMemory(db)
    assert await memory.count("s1") == 0
    for i in range(3):
        await memory.append("s1", "user", f"m{i}")
    await memory.append("s2", "user", "other")
    assert await memory.count("s1") == 3

    deleted = await memory.clear("s1")
    assert deleted == 3
    assert await memory.count("s1") == 0
    assert await memory.count("s2") == 1  # 不影响其他会话
