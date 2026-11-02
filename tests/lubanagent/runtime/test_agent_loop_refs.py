"""U2 环集成验收：FakeLLM 剧本走真实 Run——spill 后伴生工具读回全文并完成回答。"""

from lubanagent.core import EndReason, KnowledgeConfig, MemoryConfig, RunStatus
from lubanagent.memory import LongTermMemory
from lubanagent.persistence import TraceRepository
from lubanagent.runtime import RunRequest

from .conftest import seed_fake_brain
from .test_capabilities import create_agent
from .test_capabilities_refs import _setup_knowledge


async def test_knowledge_spill_read_back_full_text(db, registry, make_runtime, tmp_path):
    """小预算逼 spill → 模型调 knowledge_read 取回全文 → Run COMPLETED。"""
    setup = _setup_knowledge(db, registry)
    _store, connector = await setup(tmp_path)
    await create_agent(
        db,
        knowledge=KnowledgeConfig(
            sources=["faq"], top_k=3, inline_budget_chars=10
        ),
    )
    # 找一个真实 chunk ref 写进剧本（不猜自增 id）
    snippets = await connector.retrieve("如何申请退货退款", top_k=3)
    ref = f"kn:{snippets[0].metadata['chunk_id']}"
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "knowledge_read", "args": {"ref_id": ref}}]},
            "根据全文回答完成",
        ],
    )
    runtime = make_runtime()
    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="如何申请退货退款")
    )
    assert run.status is RunStatus.COMPLETED
    assert run.end_reason is EndReason.FINAL_ANSWER
    assert run.output == "根据全文回答完成"
    async with db.session() as session:
        events = await TraceRepository(session).list_events(run.id)
    tool_events = [e for e in events if e.type.value == "tool_call_completed"]
    assert tool_events, "knowledge_read 应产生 tool_call_completed 事件"
    payload = tool_events[0].payload
    assert payload.get("tool") == "knowledge_read"


async def test_memory_spill_read_back_full_text(db, registry, make_runtime):
    """长期记忆 spill → 模型调 memory_read 取回条目全文 → COMPLETED。"""
    fact_id = await LongTermMemory(db).remember("test-agent", "user-1", "喜欢喝美式咖啡")
    await create_agent(
        db,
        memory=MemoryConfig(session=False, long_term=True, long_term_inline_budget_chars=5),
    )
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "memory_read", "args": {"ref_id": f"mem:{fact_id}"}}]},
            "已了解用户偏好",
        ],
    )
    runtime = make_runtime(user_key_fn=lambda request: "user-1")
    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="我喜欢喝什么", user_id="user-1")
    )
    assert run.status is RunStatus.COMPLETED
    assert run.output == "已了解用户偏好"


async def test_budget_off_no_companion_tools(db, registry, make_runtime, tmp_path):
    """预算关闭 → 伴生工具不注入，存量路径 Run 正常完成（零行为变化）。"""
    setup = _setup_knowledge(db, registry)
    _store, _connector = await setup(tmp_path)
    await create_agent(
        db,
        knowledge=KnowledgeConfig(sources=["faq"], top_k=3),
    )
    await seed_fake_brain(db, script=["直接回答"])
    runtime = make_runtime()
    run = await runtime.run(
        RunRequest(agent_key="test-agent", user_input="如何申请退货退款")
    )
    assert run.status is RunStatus.COMPLETED
    assert run.output == "直接回答"
