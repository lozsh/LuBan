"""D3-int 能力接线验收：默认关闭回归 / 记忆跨轮 / RAG / Skill / 知识源缺失 / finalize 回写。"""

from pathlib import Path
from typing import Any, ClassVar

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy import select

from lubanagent.core import (
    AgentDefinition,
    EndReason,
    ErrorCode,
    KnowledgeConfig,
    LubanError,
    MemoryConfig,
    RunStatus,
    TraceEventType,
)
from lubanagent.extension import Registry
from lubanagent.knowledge import FakeEmbedding, KnowledgeStore, SqlKnowledgeConnector, ingest_file
from lubanagent.memory import FakeSummarizer, LongTermMemory, SessionMemory
from lubanagent.persistence import AgentRepository
from lubanagent.persistence.models.memory import SessionMessageRow
from lubanagent.runtime import RunRequest
from lubanagent.runtime.capabilities import CapabilityContext, prepare
from lubanagent.skill import SkillRegistry

from .conftest import RuntimeFakeChatModel, seed_fake_brain

# 与 knowledge 测试同风格的 FAQ 语料：多段重复保证切块，查询命中退货段
_TUI = "退货政策：支持七天无理由退货，退货运费由商家承担，退款原路返回，退货申请在订单页提交。"
_FA = "发货时间：工作日十六点前下单当日发出，其余订单次日发出，偏远地区顺延，节假日另行通知。"
FAQ_TEXT = "# 产品 FAQ\n\n" + "\n\n".join([_TUI * 6, _FA * 6]) + "\n"


class RecordingFakeChatModel(RuntimeFakeChatModel):
    """记录每次模型调用收到的 messages（类级共享，install 时清空）。"""

    received: ClassVar[list[list[BaseMessage]]] = []

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).received.append(list(messages))
        async for chunk in super()._astream(
            messages, stop=stop, run_manager=run_manager, **kwargs
        ):
            yield chunk

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).received.append(list(messages))
        return await super()._agenerate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )


def install_recording_model(monkeypatch, scripts: list[list[Any]]) -> None:
    """让 agent_loop 的 create_chat_model 依次吐出带脚本的记录型 fake。"""
    RecordingFakeChatModel.received.clear()
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: RecordingFakeChatModel(script=scripts.pop(0)),
    )


async def create_agent(
    db,
    *,
    key: str = "test-agent",
    memory: MemoryConfig | None = None,
    knowledge: KnowledgeConfig | None = None,
    skills: list[str] | None = None,
) -> AgentDefinition:
    """create_test_agent 的能力版：可配 memory/knowledge/skills。"""
    definition = AgentDefinition(
        key=key,
        name=key,
        instructions="你是测试助手。",
        memory=memory or MemoryConfig(),
        knowledge=knowledge or KnowledgeConfig(),
        skills=skills or [],
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    return definition


def write_skill(root: Path, name: str, *, description: str, body: str) -> Path:
    """写一个符合 Agent Skills 开放标准的最小包。"""
    package_dir = root / name
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return package_dir


def system_contents(messages: list[BaseMessage]) -> list[str]:
    return [m.content for m in messages if isinstance(m, SystemMessage)]


# ------------------------------------------------------------ 行为 1：默认关闭回归


async def test_prepare_default_off_returns_empty(db, registry):
    """全默认定义（无 session_id）→ prepare 显式 early-return，全空 CapabilityContext。"""
    definition = AgentDefinition(key="a", name="a")
    ctx = await prepare(
        definition,
        user_input="你好",
        session_id=None,
        user_key=None,
        db=db,
        registry=registry,
    )
    assert ctx == CapabilityContext()
    assert ctx.system_suffix == ""
    assert ctx.extra_tools == []
    assert ctx.history is None


async def test_default_off_run_event_sequence_unchanged(db, registry, make_runtime, monkeypatch):
    """能力全默认的 Run（含 session_id）事件序列与接线前逐事件一致。"""
    await seed_fake_brain(db, script=["占位"])
    await create_agent(db)
    install_recording_model(monkeypatch, [["最终答案"]])
    runtime = make_runtime()
    request = RunRequest(agent_key="test-agent", user_input="你好", session_id="sess-1")

    events = [event async for event in runtime.stream(request)]
    types = [event.type for event in events]

    assert types == [
        TraceEventType.RUN_STARTED,
        TraceEventType.MODEL_CALL_STARTED,
        TraceEventType.MODEL_TOKEN_DELTA,
        TraceEventType.MODEL_CALL_COMPLETED,
        TraceEventType.RUN_COMPLETED,
    ]
    # 模型输入与接线前一致：System（无 suffix）+ Human（无历史）
    [messages] = RecordingFakeChatModel.received
    assert [type(m) for m in messages] == [SystemMessage, HumanMessage]
    assert messages[0].content == "你是测试助手。"
    assert messages[1].content == "你好"


# ------------------------------------------------------------ 行为 2：会话记忆跨轮


async def test_session_memory_carries_across_runs(db, registry, make_runtime, monkeypatch):
    """memory.session=True：同 session_id 连跑两次，第二次模型输入含第一轮问答。"""
    await seed_fake_brain(db, script=["占位"])
    await create_agent(db, memory=MemoryConfig(session=True))
    install_recording_model(monkeypatch, [["第一轮回答"], ["第二轮回答"]])
    runtime = make_runtime()

    await runtime.run(
        RunRequest(agent_key="test-agent", user_input="第一轮问题", session_id="s1")
    )
    await runtime.run(
        RunRequest(agent_key="test-agent", user_input="第二轮问题", session_id="s1")
    )

    assert len(RecordingFakeChatModel.received) == 2
    second = RecordingFakeChatModel.received[1]
    assert isinstance(second[0], SystemMessage)
    roles = [(type(m), m.content) for m in second[1:]]
    assert roles == [
        (HumanMessage, "第一轮问题"),
        (AIMessage, "第一轮回答"),
        (HumanMessage, "第二轮问题"),
    ]


# ------------------------------------------------------------ 行为 3：RAG 注入


async def test_knowledge_snippets_injected_into_system_prompt(
    db, registry, make_runtime, monkeypatch, tmp_path
):
    """knowledge.sources=["faq"] → system prompt 尾部含命中片段与 [1] 编号。"""
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    embedder = FakeEmbedding()
    store = KnowledgeStore(db)
    await ingest_file(path, source="faq", store=store, embedder=embedder)
    registry.register_knowledge(
        "faq", SqlKnowledgeConnector("faq", store=store, embedder=embedder)
    )
    await seed_fake_brain(db, script=["占位"])
    await create_agent(db, knowledge=KnowledgeConfig(sources=["faq"], top_k=3))
    install_recording_model(monkeypatch, [["支持七天无理由退货"]])
    runtime = make_runtime()

    await runtime.run(RunRequest(agent_key="test-agent", user_input="如何申请退货退款"))

    [messages] = RecordingFakeChatModel.received
    [system] = system_contents(messages)
    assert system.startswith("你是测试助手。")  # 稳定前缀不变，能力内容动态后置
    assert "[1]" in system
    assert "退货" in system
    assert "引用" in system  # 引用编号说明句


# ------------------------------------------------------------ 行为 4：Skill 工具


async def test_skill_load_tool_flow(db, registry, make_runtime, monkeypatch, tmp_path):
    """skills=["refund-sop"] → 元数据入 system prompt；模型调 skill_load 取回正文。"""
    write_skill(
        tmp_path / "skills",
        "refund-sop",
        description="退款处理标准流程",
        body="先安抚用户，再核对订单，最后发起退款。",
    )
    skill_registry = SkillRegistry(skills_root=tmp_path / "skills")
    await skill_registry.scan()
    await seed_fake_brain(db, script=["占位"])
    await create_agent(db, skills=["refund-sop"])
    install_recording_model(
        monkeypatch,
        [
            [  # 本 Run 一个模型实例：先发起 tool_call，再输出正文
                {
                    "tool_calls": [
                        {"name": "skill_load", "args": {"name": "refund-sop"}, "id": "c1"}
                    ]
                },
                "已按 SOP 完成退款",
            ]
        ],
    )
    runtime = make_runtime(skill_registry=skill_registry)

    events = [
        event
        async for event in runtime.stream(
            RunRequest(agent_key="test-agent", user_input="我要退款")
        )
    ]

    assert events[-1].type is TraceEventType.RUN_COMPLETED
    assert events[-1].payload["output"] == "已按 SOP 完成退款"
    # 工具清单含 skill_load 且正文返回
    tool_done = next(e for e in events if e.type is TraceEventType.TOOL_CALL_COMPLETED)
    assert tool_done.payload["tool"] == "skill_load"
    assert "先安抚用户" in tool_done.payload["result"]["body"]
    # L1 元数据入 system prompt
    [system] = system_contents(RecordingFakeChatModel.received[0])
    assert "- refund-sop: 退款处理标准流程" in system


async def test_skill_load_conflict_renamed(db, registry, caplog, tmp_path):
    """契约决策 3：用户已注册 skill_load → 框架版改名 luban:skill_load 并记 warning。"""
    from lubanagent.extension import tool

    @tool(registry=registry, key="skill_load", description="用户自己的加载工具")
    def user_skill_load(name: str) -> str:
        return name

    skill_registry = SkillRegistry(skills_root=tmp_path)  # 空目录即可，make_load_tool 不依赖扫描
    definition = AgentDefinition(key="a", name="a", skills=["refund-sop"])

    with caplog.at_level("WARNING", logger="lubanagent.runtime.capabilities"):
        ctx = await prepare(
            definition,
            user_input="hi",
            session_id=None,
            user_key=None,
            db=db,
            registry=registry,
            skill_registry=skill_registry,
        )

    assert [t.descriptor.key for t in ctx.extra_tools] == ["luban:skill_load"]
    assert "luban:skill_load" in caplog.text


# ------------------------------------------------------------ 行为 5：知识源缺失显式失败


async def test_missing_knowledge_source_fails_run(db, registry, make_runtime):
    """定义引用未注册 source → Run FAILED 且 error=CONFIG_ERROR（不静默降级）。"""
    await seed_fake_brain(db, script=["占位"])
    await create_agent(db, knowledge=KnowledgeConfig(sources=["nope"]))
    runtime = make_runtime()

    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))

    assert run.status is RunStatus.FAILED
    assert run.end_reason is EndReason.ERROR
    assert run.error["code"] == "CONFIG_ERROR"
    assert run.error["details"] == {"source": "nope"}


# ------------------------------------------------------------ 行为 6：长期记忆


async def test_long_term_memory_injected_with_user_key(
    db, registry, make_runtime, monkeypatch
):
    """memory.long_term=True + user_key_fn → suffix 含 facts；无 user_key → 跳过。"""
    await seed_fake_brain(db, script=["占位"])
    await create_agent(db, memory=MemoryConfig(session=False, long_term=True))
    await LongTermMemory(db).remember("test-agent", "user-1", "用户偏好：只接受顺丰发货")

    # 有 user_key_fn：注入
    install_recording_model(monkeypatch, [["好的"]])
    runtime = make_runtime(user_key_fn=lambda request: "user-1")
    await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))
    [system] = system_contents(RecordingFakeChatModel.received[0])
    assert "已知用户信息" in system
    assert "用户偏好：只接受顺丰发货" in system

    # 无 user_key_fn：显式 CONFIG_ERROR（不静默跳过，H09）
    install_recording_model(monkeypatch, [["好的"]])
    runtime_no_key = make_runtime()
    run = await runtime_no_key.run(RunRequest(agent_key="test-agent", user_input="你好"))
    assert run.status is RunStatus.FAILED
    assert run.error is not None and run.error["code"] == ErrorCode.CONFIG_ERROR.value
    assert "user_key" in run.error["message"]


# ------------------------------------------------------------ H09：能力缺失显式失败


async def test_skills_without_registry_raises_config_error(db):
    """skills 非空且未注入 SkillRegistry → CONFIG_ERROR，不静默"看似开启"（H09）。"""
    from lubanagent.runtime.capabilities import prepare as _prepare

    definition = AgentDefinition(key="a", name="a", skills=["refund-sop"])
    with pytest.raises(LubanError) as exc_info:
        await _prepare(
            definition,
            user_input="你好",
            session_id=None,
            user_key=None,
            db=db,
            registry=Registry(),
            skill_registry=None,
        )
    assert exc_info.value.code is ErrorCode.CONFIG_ERROR
    assert "SkillRegistry" in exc_info.value.message


async def test_long_term_prepare_missing_user_key_message(db):
    """prepare 直接调用：long_term 开且 user_key=None → CONFIG_ERROR 指引注入。"""
    from lubanagent.runtime.capabilities import prepare as _prepare

    definition = AgentDefinition(
        key="a", name="a", memory=MemoryConfig(long_term=True)
    )
    with pytest.raises(LubanError) as exc_info:
        await _prepare(
            definition,
            user_input="你好",
            session_id=None,
            user_key=None,
            db=db,
            registry=Registry(),
        )
    assert exc_info.value.code is ErrorCode.CONFIG_ERROR
    assert "user_key" in exc_info.value.message


# ------------------------------------------------------------ finalize 回写


async def test_finalize_appends_pair_and_compresses(db, registry, make_runtime):
    """summary=True：append user+assistant 两条（带 run_id），且 maybe_compress 触发 summarizer。"""
    summarizer = FakeSummarizer()
    await seed_fake_brain(db, script=["好的"])
    await create_agent(db, memory=MemoryConfig(session=True, summary=True, window_size=1))
    runtime = make_runtime(summarizer=summarizer)

    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="记住这句话", session_id="s1")
    )
    assert run.status is RunStatus.COMPLETED

    async with db.session() as session:
        rows = list(
            (
                await session.execute(
                    select(SessionMessageRow)
                    .where(SessionMessageRow.session_id == "s1")
                    .order_by(SessionMessageRow.id)
                )
            )
            .scalars()
            .all()
        )
    assert [(row.role, row.content, row.run_id) for row in rows] == [
        ("user", "记住这句话", run.id),
        ("assistant", "好的", run.id),
    ]
    # window_size=1，两条消息超窗 → 最旧一条连同摘要压缩，summarizer 被调一次
    assert len(summarizer.calls) == 1


async def test_finalize_failure_does_not_block_run(db, registry, make_runtime, monkeypatch):
    """记忆回写异常只记日志：Run 仍 COMPLETED。"""
    await seed_fake_brain(db, script=["好的"])
    await create_agent(db, memory=MemoryConfig(session=True))

    async def boom(self, session_id, role, content, *, run_id=None):
        raise RuntimeError("DB 挂了")

    monkeypatch.setattr(SessionMemory, "append", boom)
    runtime = make_runtime()

    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="你好", session_id="s1")
    )
    assert run.status is RunStatus.COMPLETED
    assert run.output == "好的"


async def test_summary_without_summarizer_degrades_gracefully(
    db, registry, make_runtime, caplog
):
    """summary=True 但未配 summarizer → 记 warning 退化为会话记忆，不炸。"""
    await seed_fake_brain(db, script=["好的"])
    await create_agent(db, memory=MemoryConfig(session=True, summary=True, window_size=5))
    runtime = make_runtime()  # 不传 summarizer

    with caplog.at_level("WARNING", logger="lubanagent.runtime.capabilities"):
        run = await runtime.run(
            RunRequest(agent_key="test-agent", user_input="你好", session_id="s1")
        )

    assert run.status is RunStatus.COMPLETED
    assert "summarizer" in caplog.text


# ------------------------------------------------------------ 行为 3b：防错答守门（代码强制）


async def _setup_faq_source(db, registry, tmp_path):
    """灌入 FAQ 知识源并注册 connector（与行为 3 用例同语料）。"""
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    embedder = FakeEmbedding()
    store = KnowledgeStore(db)
    await ingest_file(path, source="faq", store=store, embedder=embedder)
    registry.register_knowledge(
        "faq", SqlKnowledgeConnector("faq", store=store, embedder=embedder)
    )
    return store


async def test_score_threshold_filters_weak_snippets(db, registry, tmp_path):
    """score_threshold 高于弱相关命中 → 片段被丢弃，no_hit 标记置位、suffix 为空。"""
    await _setup_faq_source(db, registry, tmp_path)
    definition = AgentDefinition(
        key="a",
        name="a",
        knowledge=KnowledgeConfig(
            sources=["faq"], top_k=3, score_threshold=0.9, no_hit_fallback="未覆盖"
        ),
    )
    ctx = await prepare(
        definition,
        user_input="如何申请退货退款",
        session_id=None,
        user_key=None,
        db=db,
        registry=registry,
    )
    assert ctx.knowledge_no_hit is True
    assert ctx.system_suffix == ""


async def test_default_config_zero_behavior_unchanged(db, registry, tmp_path):
    """三新字段全默认 → 不过滤不短路，与旧行为一致（存量零破坏底线）。"""
    await _setup_faq_source(db, registry, tmp_path)
    definition = AgentDefinition(
        key="a", name="a", knowledge=KnowledgeConfig(sources=["faq"], top_k=3)
    )
    ctx = await prepare(
        definition,
        user_input="如何申请退货退款",
        session_id=None,
        user_key=None,
        db=db,
        registry=registry,
    )
    assert ctx.knowledge_no_hit is False
    assert "[1]" in ctx.system_suffix
    assert "退货" in ctx.system_suffix


async def test_cite_false_omits_numbering_and_strict_header(db, registry, tmp_path):
    """cite=False → 无 [n] 编号、无引用约束说明句，纯片段罗列。"""
    await _setup_faq_source(db, registry, tmp_path)
    definition = AgentDefinition(
        key="a",
        name="a",
        knowledge=KnowledgeConfig(sources=["faq"], top_k=3, cite=False),
    )
    ctx = await prepare(
        definition,
        user_input="如何申请退货退款",
        session_id=None,
        user_key=None,
        db=db,
        registry=registry,
    )
    assert "[1]" not in ctx.system_suffix
    assert "编号" not in ctx.system_suffix
    assert "退货" in ctx.system_suffix


async def test_cite_true_strict_citation_constraints(db, registry, tmp_path):
    """cite=True → 强引用约束话术（只允许基于片段作答/必须回答不知道）。"""
    await _setup_faq_source(db, registry, tmp_path)
    definition = AgentDefinition(
        key="a", name="a", knowledge=KnowledgeConfig(sources=["faq"], top_k=3)
    )
    ctx = await prepare(
        definition,
        user_input="如何申请退货退款",
        session_id=None,
        user_key=None,
        db=db,
        registry=registry,
    )
    assert "只允许基于以下编号片段作答" in ctx.system_suffix
    assert "必须直接回答不知道" in ctx.system_suffix


async def test_max_age_days_filters_stale_chunks(db, registry, tmp_path):
    """metadata.ingested_at 超龄 → 丢弃；无 ingested_at 的存量 chunk 保留。"""
    embedder = FakeEmbedding()
    store = KnowledgeStore(db)
    stale = "退货政策：支持七天无理由退货。" * 10
    fresh = "发货时间：工作日十六点前下单当日发出。" * 10
    embeddings = await embedder.embed([stale, fresh])
    old_stamp = "2020-01-01T00:00:00+00:00"
    await store.add_chunks(
        "faq",
        [
            {
                "chunk_index": 0,
                "content": stale,
                "embedding": embeddings[0],
                "metadata": {"ingested_at": old_stamp},
            },
            {
                "chunk_index": 1,
                "content": fresh,
                "embedding": embeddings[1],
                "metadata": {},  # 存量 chunk 无时间戳 → 保留
            },
        ],
    )
    registry.register_knowledge(
        "faq", SqlKnowledgeConnector("faq", store=store, embedder=embedder)
    )
    definition = AgentDefinition(
        key="a",
        name="a",
        knowledge=KnowledgeConfig(sources=["faq"], top_k=3, max_age_days=30),
    )
    ctx = await prepare(
        definition,
        user_input="如何申请退货退款",
        session_id=None,
        user_key=None,
        db=db,
        registry=registry,
    )
    assert "退货" not in ctx.system_suffix  # 超龄 chunk 被丢弃
    assert "发货" in ctx.system_suffix  # 无时间戳存量 chunk 保留


async def test_no_hit_short_circuits_run_without_model_call(
    db, registry, make_runtime, monkeypatch, tmp_path
):
    """全 miss + no_hit_fallback → 不调模型，output=兜底话术，end_reason=knowledge_no_hit。"""
    await _setup_faq_source(db, registry, tmp_path)
    await seed_fake_brain(db, script=["占位"])
    await create_agent(
        db,
        knowledge=KnowledgeConfig(
            sources=["faq"],
            top_k=3,
            score_threshold=0.1,
            no_hit_fallback="抱歉，知识库未覆盖该问题。",
        ),
    )
    install_recording_model(monkeypatch, [["不应被调用"]])
    runtime = make_runtime()

    events = [
        event
        async for event in runtime.stream(
            RunRequest(agent_key="test-agent", user_input="今天天气怎么样")
        )
    ]

    types = [event.type for event in events]
    assert types == [
        TraceEventType.RUN_STARTED,
        TraceEventType.KNOWLEDGE_NO_HIT,
        TraceEventType.RUN_COMPLETED,
    ]
    assert RecordingFakeChatModel.received == []  # 模型零调用（零 token 消耗）
    terminal = events[-1].payload
    assert terminal["end_reason"] == "knowledge_no_hit"
    assert terminal["output"] == "抱歉，知识库未覆盖该问题。"
    no_hit_payload = events[1].payload
    assert no_hit_payload["sources"] == ["faq"]
    assert no_hit_payload["score_threshold"] == 0.1
    # 过滤前后条数（阈值校准排障观测点）：检索有原始命中、全被阈值过滤
    assert no_hit_payload["hits_raw"] > 0
    assert no_hit_payload["hits_kept"] == 0


async def test_no_hit_fallback_written_to_session_memory(
    db, registry, make_runtime, monkeypatch, tmp_path
):
    """短路拒答的兜底话术仍经 finalize 回写会话记忆（user + assistant 两条）。"""
    await _setup_faq_source(db, registry, tmp_path)
    await seed_fake_brain(db, script=["占位"])
    await create_agent(
        db,
        memory=MemoryConfig(session=True),
        knowledge=KnowledgeConfig(
            sources=["faq"], top_k=3, score_threshold=0.1, no_hit_fallback="兜底话术"
        ),
    )
    install_recording_model(monkeypatch, [["不应被调用"]])
    runtime = make_runtime()

    await runtime.run(
        RunRequest(agent_key="test-agent", user_input="今天天气怎么样", session_id="s-guard")
    )

    async with db.session() as session:
        rows = (
            await session.scalars(
                select(SessionMessageRow).where(SessionMessageRow.session_id == "s-guard")
            )
        ).all()
    assert [(row.role, row.content) for row in rows] == [
        ("user", "今天天气怎么样"),
        ("assistant", "兜底话术"),
    ]


async def test_relevant_query_still_answers_with_threshold(
    db, registry, make_runtime, monkeypatch, tmp_path
):
    """阈值 + 兜底配置下，强相关查询仍走模型正常作答（宁拒不答不误杀正常问答）。"""
    await _setup_faq_source(db, registry, tmp_path)
    await seed_fake_brain(db, script=["占位"])
    await create_agent(
        db,
        knowledge=KnowledgeConfig(
            sources=["faq"], top_k=3, score_threshold=0.1, no_hit_fallback="兜底话术"
        ),
    )
    install_recording_model(monkeypatch, [["支持七天无理由退货"]])
    runtime = make_runtime()

    events = [
        event
        async for event in runtime.stream(
            RunRequest(agent_key="test-agent", user_input="如何申请退货退款")
        )
    ]

    types = [event.type for event in events]
    assert TraceEventType.KNOWLEDGE_NO_HIT not in types
    assert TraceEventType.MODEL_CALL_COMPLETED in types
    assert len(RecordingFakeChatModel.received) == 1
    assert events[-1].payload["output"] == "支持七天无理由退货"
