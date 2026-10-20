"""LongTermMemory：remember/recall 跨 session、limit 与排序、forget、format_facts_for_prompt。"""

from lubanagent.memory import LongTermMemory, format_facts_for_prompt
from lubanagent.persistence import Database


async def test_remember_returns_id_and_recall_roundtrip(db: Database) -> None:
    memory = LongTermMemory(db)
    fact_id = await memory.remember("agent-a", "user-1", "用户偏好中文回复")
    assert isinstance(fact_id, int)
    assert fact_id > 0

    assert await memory.recall("agent-a", "user-1") == ["用户偏好中文回复"]


async def test_recall_is_cross_session(db: Database) -> None:
    """长期记忆与 session_id 无关：显式写入后任意会话可取（代码强制）。"""
    memory = LongTermMemory(db)
    await memory.remember("agent-a", "user-1", "fact-from-session-1")
    # 另一个“会话”里用同一 agent+user recall（LongTermMemory 本就不带 session 维度）
    assert await memory.recall("agent-a", "user-1") == ["fact-from-session-1"]
    # 其他 user / agent 隔离
    assert await memory.recall("agent-a", "user-2") == []
    assert await memory.recall("agent-b", "user-1") == []


async def test_recall_limit_takes_latest_then_reverses(db: Database) -> None:
    memory = LongTermMemory(db)
    for i in range(5):
        await memory.remember("agent-a", "user-1", f"fact-{i}")

    facts = await memory.recall("agent-a", "user-1", limit=3)
    assert facts == ["fact-2", "fact-3", "fact-4"]  # 最近 3 条，正序返回
    assert len(await memory.recall("agent-a", "user-1")) == 5  # 默认 limit=20 全覆盖


async def test_forget(db: Database) -> None:
    memory = LongTermMemory(db)
    keep = await memory.remember("agent-a", "user-1", "保留")
    drop = await memory.remember("agent-a", "user-1", "删除我")

    assert await memory.forget("agent-a", "user-1", drop) is True
    assert await memory.recall("agent-a", "user-1") == ["保留"]

    # 已删 / 越权（别的 user）删不到
    assert await memory.forget("agent-a", "user-1", drop) is False
    assert await memory.forget("agent-a", "user-2", keep) is False
    assert await memory.recall("agent-a", "user-1") == ["保留"]


def test_format_facts_for_prompt() -> None:
    assert format_facts_for_prompt([]) == ""
    assert format_facts_for_prompt(["甲", "乙"]) == "已知用户信息：\n- 甲\n- 乙"


async def test_capacity_evicts_oldest(db: Database) -> None:
    """容量上限：超出后淘汰最旧，recall 只剩最近 capacity 条。"""
    memory = LongTermMemory(db, capacity=3)
    for i in range(5):
        await memory.remember("agent-a", "user-1", f"fact-{i}")

    assert await memory.recall("agent-a", "user-1", limit=100) == [
        "fact-2",
        "fact-3",
        "fact-4",
    ]


async def test_capacity_is_per_agent_user(db: Database) -> None:
    """容量按 agent+user 维度独立计数：一方淘汰不影响另一方。"""
    memory = LongTermMemory(db, capacity=2)
    for i in range(4):
        await memory.remember("agent-a", "user-1", f"u1-{i}")
    await memory.remember("agent-a", "user-2", "u2-0")
    await memory.remember("agent-a", "user-2", "u2-1")

    assert await memory.recall("agent-a", "user-1", limit=100) == ["u1-2", "u1-3"]
    assert await memory.recall("agent-a", "user-2", limit=100) == ["u2-0", "u2-1"]


async def test_capacity_below_one_clamped_to_one(db: Database) -> None:
    """capacity < 1 钳到 1：至少保留最新一条，不会清空。"""
    memory = LongTermMemory(db, capacity=0)
    await memory.remember("agent-a", "user-1", "first")
    await memory.remember("agent-a", "user-1", "second")

    assert await memory.recall("agent-a", "user-1", limit=100) == ["second"]


async def test_default_capacity_small_history_untouched(db: Database) -> None:
    """默认容量（Settings=1000）下少量写入零感知：一条不淘汰（存量兼容）。"""
    memory = LongTermMemory(db)  # capacity=None → Settings 默认
    for i in range(10):
        await memory.remember("agent-a", "user-1", f"fact-{i}")

    assert await memory.recall("agent-a", "user-1", limit=100) == [
        f"fact-{i}" for i in range(10)
    ]

