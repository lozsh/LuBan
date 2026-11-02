"""U2 能力装配验收：预算 None 零行为变化 / spill 引用行 / 伴生工具越权防线 / 命名冲突。"""

from lubanagent.core import KnowledgeConfig, MemoryConfig
from lubanagent.extension import tool
from lubanagent.knowledge import (
    FakeEmbedding,
    KnowledgeStore,
    SqlKnowledgeConnector,
    ingest_file,
)
from lubanagent.memory import LongTermMemory
from lubanagent.runtime.capabilities import prepare
from lubanagent.runtime.context_tools import (
    make_knowledge_read_tool,
    make_memory_read_tool,
)

from .test_capabilities import FAQ_TEXT, create_agent  # 复用语料与 Agent 工厂

# ------------------------------------------------------------ 伴生工具单测（越权防线）


async def test_knowledge_read_allows_within_sources(db, tmp_path):
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    store = KnowledgeStore(db)
    await ingest_file(path, source="faq", store=store, embedder=FakeEmbedding())
    chunk = (await store.list_chunks(source="faq"))[0]
    tool = make_knowledge_read_tool(store, allowed_sources=["faq"])
    result = await tool.handler(ref_id=f"kn:{chunk['id']}")
    assert result["content"]
    assert result["ref_id"] == "kn:" + str(chunk["id"])


async def test_knowledge_read_denies_foreign_source(db, tmp_path):
    """source 不在 allowed_sources → not_found（不泄露存在性）。"""
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    store = KnowledgeStore(db)
    await ingest_file(path, source="faq", store=store, embedder=FakeEmbedding())
    chunk = (await store.list_chunks(source="faq"))[0]
    tool = make_knowledge_read_tool(store, allowed_sources=["other"])
    assert await tool.handler(ref_id=f"kn:{chunk['id']}") == {"error": "not_found"}


async def test_knowledge_read_bad_ref(db, tmp_path):
    store = KnowledgeStore(db)
    tool = make_knowledge_read_tool(store, allowed_sources=["faq"])
    assert await tool.handler(ref_id="kn:abc") == {"error": "not_found"}
    assert await tool.handler(ref_id="kn:99999") == {"error": "not_found"}


async def test_memory_read_scope_and_denial(db):
    memory = LongTermMemory(db)
    fact_id = await memory.remember("agent-a", "user-1", "喜欢喝美式")
    tool = make_memory_read_tool(db, agent_key="agent-a", user_key="user-1")
    assert (await tool.handler(ref_id=f"mem:{fact_id}"))["content"] == "喜欢喝美式"
    # 跨 agent / 跨 user 越权 → not_found
    cross_agent = make_memory_read_tool(db, agent_key="agent-b", user_key="user-1")
    cross_user = make_memory_read_tool(db, agent_key="agent-a", user_key="user-2")
    assert await cross_agent.handler(ref_id=f"mem:{fact_id}") == {"error": "not_found"}
    assert await cross_user.handler(ref_id=f"mem:{fact_id}") == {"error": "not_found"}


# ------------------------------------------------------------ prepare 装配


def _setup_knowledge(db, registry):
    async def setup(tmp_path):
        path = tmp_path / "faq.md"
        path.write_text(FAQ_TEXT, encoding="utf-8")
        embedder = FakeEmbedding()
        store = KnowledgeStore(db)
        await ingest_file(path, source="faq", store=store, embedder=embedder)
        connector = SqlKnowledgeConnector("faq", store=store, embedder=embedder)
        registry.register_knowledge("faq", connector)
        return store, connector

    return setup


async def test_budget_none_keeps_legacy_suffix(db, registry, tmp_path):
    """预算 None → suffix 与旧编号格式逐字符一致（零行为变化的结构性断言）。"""
    setup = _setup_knowledge(db, registry)
    _store, connector = await setup(tmp_path)
    definition = await create_agent(
        db,
        knowledge=KnowledgeConfig(sources=["faq"], top_k=3),
    )
    ctx = await prepare(
        definition,
        user_input="如何申请退货退款",
        session_id=None,
        user_key=None,
        db=db,
        registry=registry,
    )
    snippets = await connector.retrieve("如何申请退货退款", top_k=3)
    from lubanagent.knowledge import format_snippets_for_prompt

    legacy = format_snippets_for_prompt(snippets)
    assert ctx.system_suffix.endswith(legacy)
    assert "〔ref:" not in ctx.system_suffix
    assert ctx.extra_tools == []


async def test_budget_forces_spill_and_injects_read_tool(db, registry, tmp_path):
    """小预算 → 全部 spill 出引用行，knowledge_read 伴生工具注入 extra_tools。"""
    setup = _setup_knowledge(db, registry)
    _store, connector = await setup(tmp_path)
    definition = await create_agent(
        db,
        knowledge=KnowledgeConfig(
            sources=["faq"], top_k=3, inline_budget_chars=10
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
    assert ctx.system_suffix.count("〔ref: kn:") == 2
    assert "knowledge_read 获取全文" in ctx.system_suffix
    assert [t.descriptor.key for t in ctx.extra_tools] == ["knowledge_read"]
    # ref_id 与真实 chunk id 对得上（读回端可解析）
    snippets = await connector.retrieve("如何申请退货退款", top_k=3)
    for snippet in snippets:
        assert f"kn:{snippet.metadata['chunk_id']}" in ctx.system_suffix


async def test_budget_within_limits_no_tool_injected(db, registry, tmp_path):
    """预算充足无 spill → 不注入伴生工具（只在需要读回时装配）。"""
    setup = _setup_knowledge(db, registry)
    _store, _connector = await setup(tmp_path)
    definition = await create_agent(
        db,
        knowledge=KnowledgeConfig(
            sources=["faq"], top_k=3, inline_budget_chars=100_000
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
    assert "〔ref: kn:" in ctx.system_suffix  # 有预算路径仍带 ref 标注
    assert ctx.extra_tools == []


async def test_memory_budget_spill_and_tool(db, registry, tmp_path):
    """长期记忆预算 → 超预算条目出摘要引用行，memory_read 注入。"""
    await LongTermMemory(db).remember("test-agent", "user-1", "喜欢喝美式咖啡")
    await LongTermMemory(db).remember("test-agent", "user-1", "在杭州工作")
    definition = await create_agent(
        db,
        memory=MemoryConfig(
            long_term=True, long_term_inline_budget_chars=5
        ),
    )
    ctx = await prepare(
        definition,
        user_input="你好",
        session_id=None,
        user_key="user-1",
        db=db,
        registry=registry,
    )
    assert "已知用户信息：" in ctx.system_suffix
    assert ctx.system_suffix.count("〔ref: mem:") == 1
    assert "memory_read 获取全文" in ctx.system_suffix
    assert [t.descriptor.key for t in ctx.extra_tools] == ["memory_read"]


async def test_memory_budget_none_keeps_legacy(db, registry):
    """长期记忆预算 None → 旧行为（format_facts_for_prompt，无引用行）。"""
    await LongTermMemory(db).remember("test-agent", "user-1", "喜欢喝美式咖啡")
    definition = await create_agent(
        db, memory=MemoryConfig(long_term=True)
    )
    ctx = await prepare(
        definition,
        user_input="你好",
        session_id=None,
        user_key="user-1",
        db=db,
        registry=registry,
    )
    assert ctx.system_suffix == "已知用户信息：\n- 喜欢喝美式咖啡"
    assert ctx.extra_tools == []


async def test_companion_tool_renamed_on_conflict(db, registry, tmp_path):
    """用户已注册同名工具 → 框架版改名 luban:knowledge_read，引用行同步改指向。"""
    setup = _setup_knowledge(db, registry)
    _store, _connector = await setup(tmp_path)

    @tool(registry=registry, description="用户自建的同名读取工具")
    def knowledge_read(ref_id: str) -> dict:
        return {"from": "user"}

    definition = await create_agent(
        db,
        knowledge=KnowledgeConfig(
            sources=["faq"], top_k=3, inline_budget_chars=10
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
    assert "luban:knowledge_read" in ctx.system_suffix
    assert [t.descriptor.key for t in ctx.extra_tools] == ["luban:knowledge_read"]

