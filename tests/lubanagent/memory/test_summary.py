"""SummaryMemory：maybe_compress 触发/未触发两态、摘要累积、context_prefix 有/无摘要两形态。"""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select

from lubanagent.memory import FakeSummarizer, LlmSummarizer, SummaryMemory
from lubanagent.persistence import Database
from lubanagent.persistence.models.memory import SessionMessageRow, SessionSummaryRow


async def _seed(db: Database, session_id: str, n: int, *, start: int = 0) -> list[int]:
    """写入 n 条交替 user/assistant 消息（内容 msg-{start+i}），返回消息 id 升序列表。"""
    async with db.session() as session:
        ids: list[int] = []
        for i in range(n):
            row = SessionMessageRow(
                session_id=session_id,
                role="user" if (start + i) % 2 == 0 else "assistant",
                content=f"msg-{start + i}",
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
        return ids


async def test_maybe_compress_not_triggered_within_window(db: Database) -> None:
    summarizer = FakeSummarizer()
    memory = SummaryMemory(db, summarizer=summarizer)
    await _seed(db, "s1", 3)

    assert await memory.maybe_compress("s1", window_size=3) is False
    assert await memory.get_summary("s1") is None
    assert summarizer.calls == []  # 未触发不调 summarizer


async def test_maybe_compress_triggered_compresses_oldest_overflow(db: Database) -> None:
    summarizer = FakeSummarizer()
    memory = SummaryMemory(db, summarizer=summarizer)
    ids = await _seed(db, "s1", 5)

    assert await memory.maybe_compress("s1", window_size=3) is True

    # 窗口外最旧 2 条交 summarizer，previous 为 None
    assert len(summarizer.calls) == 1
    messages, previous = summarizer.calls[0]
    assert [m.content for m in messages] == ["msg-0", "msg-1"]
    assert previous is None

    # upsert 落库：upto 指向第 2 条
    async with db.session() as session:
        row = await session.scalar(
            select(SessionSummaryRow).where(SessionSummaryRow.session_id == "s1")
        )
        assert row is not None
        assert row.upto_message_id == ids[1]
        assert row.summary == "human:msg-0 | ai:msg-1"

    # 已压缩消息不物理删除
    async with db.session() as session:
        remaining = await session.scalar(
            select(SessionMessageRow.id).where(SessionMessageRow.session_id == "s1")
        )
        assert remaining is not None


async def test_maybe_compress_accumulates_previous_summary(db: Database) -> None:
    summarizer = FakeSummarizer()
    memory = SummaryMemory(db, summarizer=summarizer)
    ids = await _seed(db, "s1", 5)

    assert await memory.maybe_compress("s1", window_size=3) is True
    first_summary = await memory.get_summary("s1")

    # 再追加 2 条（未压缩数 5 > 窗口 3）→ 再压缩最旧 2 条未压缩消息，previous 传入
    await _seed(db, "s1", 2, start=5)
    assert await memory.maybe_compress("s1", window_size=3) is True

    assert len(summarizer.calls) == 2
    messages, previous = summarizer.calls[1]
    assert [m.content for m in messages] == ["msg-2", "msg-3"]
    assert previous == first_summary

    # 同一行 upsert：upto 前进到第 4 条
    async with db.session() as session:
        rows = list(
            (await session.execute(select(SessionSummaryRow))).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].upto_message_id == ids[3]
        assert first_summary in rows[0].summary  # 摘要累积


async def test_context_prefix_without_summary_is_plain_window(db: Database) -> None:
    memory = SummaryMemory(db, summarizer=FakeSummarizer())
    await _seed(db, "s1", 5)

    prefix = await memory.context_prefix("s1", window_size=3)
    assert [m.content for m in prefix] == ["msg-2", "msg-3", "msg-4"]
    assert not any(isinstance(m, SystemMessage) for m in prefix)


async def test_context_prefix_with_summary_prepends_system_message(db: Database) -> None:
    summarizer = FakeSummarizer()
    memory = SummaryMemory(db, summarizer=summarizer)
    await _seed(db, "s1", 5)
    assert await memory.maybe_compress("s1", window_size=3) is True

    prefix = await memory.context_prefix("s1", window_size=3)
    assert isinstance(prefix[0], SystemMessage)
    assert prefix[0].content.startswith("此前对话摘要：")
    assert "msg-0" in prefix[0].content
    # 后续只含 upto_message_id 之后的窗口内消息
    assert [type(m) for m in prefix[1:]] == [HumanMessage, AIMessage, HumanMessage]
    assert [m.content for m in prefix[1:]] == ["msg-2", "msg-3", "msg-4"]


async def test_context_prefix_with_summary_limits_window_after_upto(db: Database) -> None:
    summarizer = FakeSummarizer()
    memory = SummaryMemory(db, summarizer=summarizer)
    await _seed(db, "s1", 5)
    assert await memory.maybe_compress("s1", window_size=3) is True
    # 压缩后又来了 4 条新消息（upto 之后共 7 条）→ 窗口只取最新 3 条
    await _seed(db, "s1", 4, start=5)

    prefix = await memory.context_prefix("s1", window_size=3)
    assert isinstance(prefix[0], SystemMessage)
    assert [m.content for m in prefix[1:]] == ["msg-6", "msg-7", "msg-8"]


async def test_llm_summarizer_prompt_and_result() -> None:
    """LlmSummarizer：提示词要求保留事实/决定/待办，previous 并入，返回模型文本。"""
    model = GenericFakeChatModel(messages=iter(["压缩后的摘要"]))
    summarizer = LlmSummarizer(model)
    result = await summarizer.summarize([HumanMessage(content="我想退货")], previous="旧摘要")
    assert result == "压缩后的摘要"
