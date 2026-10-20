"""KV-cache 友好不变量守卫（风险登记 R1）：稳定前缀逐字节一致 + 动态后置 + append-only。

守护的不变量（技术方案 §13.6，context_builder.py 模块注释自述「尽力而为非硬约束」——
本文件把它变成测试级硬约束）：

同一 session 各轮发给模型的 messages 中，SystemMessage 的「稳定前缀」段必须逐字节一致。
稳定前缀 = role + "\\n\\n" + instructions 经变量渲染后的完整连续文本（ContextBuilder
.build_system_prompt 的产出，同 session 同 variables 下逐轮相同）；逐轮变化的动态内容
（知识检索片段 / 长期记忆 / Skill 元数据 → system prompt 尾部 suffix；会话/摘要历史 →
SystemMessage 之后的消息；本轮 user_input → 末位 HumanMessage）只允许出现在稳定前缀之后。

为什么重要：LLM 服务端 KV-cache 按前缀匹配，缓存命中要求请求 bytes 的公共前缀逐字节
稳定。一旦有人把动态内容（时间戳/轮次/检索片段）插进前缀头部或中部，缓存在第一个
变化字节处失效、命中率静默归零——不会有任何运行期报错，只有成本与延迟悄悄变差。
本测试用「测试内独立构造的期望前缀」断言连续两轮 SystemMessage 均以它逐字节开头，
此类改动会让测试红。

期望前缀刻意不经 ContextBuilder 计算：若组装器本身被塞进动态内容，用它自算期望等于
自证清白，守卫失效。注意 startswith 的语义恰好等于不变量本身——只有「尾部追加动态
内容」（KV-cache 等价安全）能通过，头部前插与中部穿插（缓存失效）都会红。

运行期检查决策：不设。组装热路径里 system_prompt 就是 ContextBuilder 的直接产出，
运行期重算再比对是同义反复；跨轮缓存前缀做断言需要 runtime 持有 per-session 状态，
热路径开销换不来超过本测试的确定性。测试级守卫足够（确定性、零运行期开销）。
"""

from typing import Any, ClassVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from lubanagent.core import AgentDefinition, KnowledgeConfig, MemoryConfig
from lubanagent.knowledge import FakeEmbedding, KnowledgeStore, SqlKnowledgeConnector, ingest_file
from lubanagent.persistence import AgentRepository
from lubanagent.runtime import ContextBuilder, RunRequest

from .conftest import RuntimeFakeChatModel, seed_fake_brain

# 与 test_capabilities 同风格的 FAQ 语料：退货段 + 发货段各重复 6 次保证切块，
# 两轮不同问题命中不同片段 → system prompt 尾部 suffix 逐轮必然变化
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


def install_recording_model(monkeypatch, scripts: list[list[Any]]) -> None:
    """让 agent_loop 的 create_chat_model 依次吐出带脚本的记录型 fake。"""
    RecordingFakeChatModel.received.clear()
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: RecordingFakeChatModel(script=scripts.pop(0)),
    )


def system_contents(messages: list[BaseMessage]) -> list[str]:
    return [m.content for m in messages if isinstance(m, SystemMessage)]


async def test_stable_prefix_byte_identical_across_session_turns(
    db, registry, make_runtime, monkeypatch, tmp_path
):
    """同一 session 连续两轮：稳定前缀逐字节一致，动态内容（suffix/历史）只增其后。

    两轮刻意构造出「全部动态维度都不同」：user_input 不同（检索 query 变 → 知识
    suffix 变）、轮次状态不同（第二轮多出第一轮问答历史）。若任何动态维度渗进
    前缀，前缀逐字节一致性即被打破。
    """
    # 语料入库 + 知识源注册（知识能力开启 → 每轮 suffix 随 query 变化）
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    embedder = FakeEmbedding()
    store = KnowledgeStore(db)
    await ingest_file(path, source="faq", store=store, embedder=embedder)
    registry.register_knowledge(
        "faq", SqlKnowledgeConnector("faq", store=store, embedder=embedder)
    )

    # 定义同时覆盖前缀的两个来源与变量渲染路径：role 段 + instructions 段 + 双来源变量
    # （definition.prompt_variables 与 request.variables 合并渲染）
    definition = AgentDefinition(
        key="kv-guard",
        name="kv-guard",
        role="你是{brand}售后客服。",
        instructions="只依据检索片段作答。品牌：{brand}，用户等级：{tier}。",
        prompt_variables={"brand": "鲁班商城"},
        memory=MemoryConfig(session=True),
        knowledge=KnowledgeConfig(sources=["faq"], top_k=3),
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)

    await seed_fake_brain(db, script=["占位"])
    install_recording_model(monkeypatch, [["第一轮回答"], ["第二轮回答"]])
    runtime = make_runtime()

    # 同 session 同 variables（variables 是调用方逐 session 稳定的配置语义，
    # 非「稳定前缀」的守护范围；守护范围 = 组装代码不得把动态内容插进前缀）
    variables = {"tier": "VIP"}
    await runtime.run(
        RunRequest(
            agent_key="kv-guard",
            user_input="如何申请退货退款",
            session_id="s1",
            variables=variables,
        )
    )
    await runtime.run(
        RunRequest(
            agent_key="kv-guard", user_input="什么时候能发货", session_id="s1", variables=variables
        )
    )

    # 期望稳定前缀：测试内独立拼出（role + "\n\n" + instructions 渲染结果），
    # 不经 ContextBuilder——防组装器自证清白
    expected_prefix = (
        "你是鲁班商城售后客服。\n\n只依据检索片段作答。品牌：鲁班商城，用户等级：VIP。"
    )

    assert len(RecordingFakeChatModel.received) == 2
    turn1, turn2 = RecordingFakeChatModel.received

    # 核心断言 1：两轮 SystemMessage 均以稳定前缀逐字节开头（startswith 语义 =
    # 只允许尾部追加动态内容；头部前插/中部穿插动态内容都会红）
    [sys1] = system_contents(turn1)
    [sys2] = system_contents(turn2)
    assert sys1.startswith(expected_prefix)
    assert sys2.startswith(expected_prefix)

    # suffix 确实注入（前缀之后还有内容，断言非空洞），且逐轮不同（query 不同 → 命中
    # 片段不同）——证明本测试真的在「动态内容存在且变化」的前提下守住前缀稳定
    assert len(sys1) > len(expected_prefix)
    assert len(sys2) > len(expected_prefix)
    assert sys1 != sys2
    assert "退货" in sys1 and "发货" in sys2

    # 核心断言 2：append-only——第二轮 SystemMessage 之后是第一轮问答原样追加 + 本轮
    # 输入；既有消息不改写、不重排
    assert [(type(m), m.content) for m in turn2[1:]] == [
        (HumanMessage, "如何申请退货退款"),
        (AIMessage, "第一轮回答"),
        (HumanMessage, "什么时候能发货"),
    ]
    # 第一轮无历史：System + 本轮 Human
    assert [type(m) for m in turn1] == [SystemMessage, HumanMessage]


def test_build_messages_append_only_never_mutates_history() -> None:
    """append-only 单元守卫：build_messages 不得原地改写入参 history。

    历史消息是上一轮已发生对话的载体，原位改写（哪怕一个字节）等价于破坏
    「前缀逐字节稳定」——本轮请求与缓存里上一轮请求的公共前缀从改动处断裂。
    """
    history = [HumanMessage(content="q1"), AIMessage(content="a1")]
    snapshot = [(type(m), m.content) for m in history]
    messages = ContextBuilder().build_messages(
        history=history, user_input="q2", window_size=10
    )
    # 入参原样（未被动过），产出 = 入参尾窗原样 + 新增一条
    assert [(type(m), m.content) for m in history] == snapshot
    assert [(type(m), m.content) for m in messages] == [
        (HumanMessage, "q1"),
        (AIMessage, "a1"),
        (HumanMessage, "q2"),
    ]
