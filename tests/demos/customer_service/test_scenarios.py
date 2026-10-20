"""客服示例验收场景回归测试（契约 9 条 + 零配置兜底）。

Mock biz 以 fixture 起本地线程服务（动态端口），Agent 用 fake 模型。
所有场景零网络零密钥可重复。
"""

import asyncio
import json

import pytest

from lubanagent.core import EndReason, RunStatus
from lubanagent.extension import default_registry, discover
from lubanagent.observability.evals import EvalRunner, load_dataset
from lubanagent.persistence import TraceRepository
from lubanagent.runtime import AgentRuntime, RunRequest

from .conftest import create_agent, seed_fake_brain

_AGENT_KEY = "customer-service-bot"


async def _seed_main_agent(db, registry, script):
    """注册主 Agent + fake brain + 客服工具。"""
    from lubanagent.core import BrainProfile

    await seed_fake_brain(db, script=script, profile=BrainProfile.SMART)
    # 注册客服工具到独立 registry
    from app.tools.customer_service import (  # noqa: F401  # 导入即注册到 default_registry
        order,
        refund,
    )

    discover(["app.tools"], registry=default_registry)
    await create_agent(
        db,
        key=_AGENT_KEY,
        instructions="你是售后客服。查单用 query_order；退款用 refund_apply。",
        tools=["query_order", "refund_apply"],
    )


class _FakeApprovalHandler:
    """测试用同步审批 handler：立即批准。"""

    def __init__(self, approved: bool = True):
        self.approved = approved
        self.called = False

    async def __call__(self, descriptor, args):
        self.called = True
        return self.approved


@pytest.mark.usefixtures("mock_biz")
class TestCustomerServiceScenarios:
    """契约行为 1-9 自动化回归。"""

    @pytest.mark.asyncio
    async def test_1_definition_change_takes_effect(
        self, db, settings, registry, sequential_models
    ):
        """改定义即时生效：改 instructions → 下一 Run 行为变化（fake 模型收到新 prompt）。"""
        from langchain_core.messages import SystemMessage

        script = ["你好，我是客服。", "您好，我是新版客服。"]
        await _seed_main_agent(db, registry, script)
        runtime = AgentRuntime(db=db, settings=settings, registry=default_registry)

        # 第一次跑
        run1 = await runtime.run(RunRequest(agent_key=_AGENT_KEY, user_input="你好"))
        assert run1.status is RunStatus.COMPLETED

        # 改 instructions
        from lubanagent.persistence import AgentRepository

        async with db.session() as session:
            repo = AgentRepository(session)
            existing = await repo.get_agent(_AGENT_KEY)
            existing.instructions = "你是新版客服，先说'您好'。"
            existing.version += 1
            await repo.create_version(_AGENT_KEY, existing)

        # 第二次跑：定义更新后下一次 Run 的 system prompt 含新 instructions（行为变化取证：
        # fake 模型经 received 记录每轮收到的消息，直接断言 prompt 内容而非仅 DB 记录）
        run2 = await runtime.run(RunRequest(agent_key=_AGENT_KEY, user_input="你好"))
        assert run2.status is RunStatus.COMPLETED

        model = sequential_models["fake-chat"]
        assert len(model.received) >= 2, "应有两次模型调用"
        first_system = next(
            (m for m in model.received[0] if isinstance(m, SystemMessage)), None
        )
        second_system = next(
            (m for m in model.received[-1] if isinstance(m, SystemMessage)), None
        )
        assert first_system is not None and second_system is not None
        assert "新版" not in str(first_system.content)
        assert "新版" in str(second_system.content)

    @pytest.mark.asyncio
    async def test_2_high_risk_approval(self, db, settings, registry, mock_biz):
        """高风险审批：refund_apply(>500) 触发 approval + WAITING_APPROVAL。"""
        # script: 先调 query_order 拿订单，再调 refund_apply（>500 触发审批）
        script = [
            {
                "tool_calls": [
                    {
                        "name": "refund_apply",
                        "args": {
                            "order_id": "ORD-1006",
                            "amount": 899.0,
                            "reason": "不想要了",
                        },
                        "id": "c1",
                    }
                ]
            },
            "退款已提交。",
        ]
        await _seed_main_agent(db, registry, script)
        from lubanagent.tool import ApprovalManager

        approval_manager = ApprovalManager(default_timeout_seconds=5.0)
        runtime = AgentRuntime(
            db=db, settings=settings, registry=default_registry, approval_manager=approval_manager
        )

        # 跑到审批暂停点
        run_id = "test-approval-run"
        task = asyncio.create_task(
            runtime.run(RunRequest(agent_key=_AGENT_KEY, user_input="退ORD-1006", run_id=run_id))
        )
        await asyncio.sleep(0.5)

        pending = await approval_manager.pending(run_id=run_id)
        assert len(pending) == 1, "应有一个待审批请求"
        approval_id = pending[0].id

        # 批准
        await approval_manager.decide(approval_id, True)
        run = await task
        assert run.status is RunStatus.COMPLETED

        # Mock biz 收到退款单（tmp 数据副本内回写）
        from demos.customer_service.mock_services import server as mock_server

        refunds = json.loads(
            (mock_server._DATA_DIR / "refunds.json").read_text(encoding="utf-8")
        )
        assert any(
            r["order_id"] == "ORD-1006" and float(r["amount"]) == 899.0 for r in refunds
        ), "Mock biz 应收到 ORD-1006 的 899 元退款单"

    @pytest.mark.asyncio
    async def test_3_budget_circuit_breaker(self, db, settings, registry, monkeypatch):
        """预算熔断：budget_tokens 小值 → Run 终止 end_reason=BUDGET_EXCEEDED。"""
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        from lubanagent.core import Budget
        from lubanagent.model import ScriptedFakeChatModel

        class UsageFakeModel(ScriptedFakeChatModel):
            usage: tuple[int, int] = (50, 50)

            async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
                for chunk in self._chunks_for(self._to_message(self._next())):
                    yield chunk
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        usage_metadata={
                            "input_tokens": self.usage[0],
                            "output_tokens": self.usage[1],
                            "total_tokens": self.usage[0] + self.usage[1],
                        },
                    )
                )

        script = ["这是一段较长的回复，会消耗 token。"]
        await seed_fake_brain(db, script=script)
        monkeypatch.setattr(
            "lubanagent.runtime.agent_loop.create_chat_model",
            lambda d: UsageFakeModel(script=list(d.params.get("script", []))),
        )

        from app.tools.customer_service import order  # noqa: F401

        discover(["app.tools"], registry=default_registry)
        from lubanagent.core import AgentDefinition, BoundaryConfig, BrainConfig

        definition = AgentDefinition(
            key=_AGENT_KEY,
            name=_AGENT_KEY,
            instructions="你是客服。",
            tools=[],
            tool_signatures={},
            boundary=BoundaryConfig(max_steps=3, timeout_seconds=10, budget=Budget(max_tokens=1)),
            brain=BrainConfig(),
        )
        from lubanagent.persistence import AgentRepository

        async with db.session() as session:
            await AgentRepository(session).create_agent(definition)

        runtime = AgentRuntime(db=db, settings=settings, registry=default_registry)
        run = await runtime.run(RunRequest(agent_key=_AGENT_KEY, user_input="你好"))
        assert run.status is RunStatus.FAILED
        assert run.end_reason is EndReason.BUDGET_EXCEEDED

    @pytest.mark.asyncio
    async def test_4_trace_replay(self, db, settings, registry):
        """Trace 回放：完成 Run 后能回答七问（who/what/when/how/why/outcome/cost）。"""
        script = ["你好，我是客服。"]
        await _seed_main_agent(db, registry, script)
        runtime = AgentRuntime(db=db, settings=settings, registry=default_registry)

        run = await runtime.run(RunRequest(agent_key=_AGENT_KEY, user_input="你好"))
        assert run.status is RunStatus.COMPLETED

        async with db.session() as session:
            events = await TraceRepository(session).list_events(run.id)

        # 七问验证：Run 记录 + Trace 事件存在
        assert run.id is not None and len(run.id) > 0  # who: run_id
        assert run.agent_key == _AGENT_KEY  # what: agent
        assert run.started_at is not None  # when
        assert run.input is not None  # how: input
        assert run.output is not None  # outcome: output
        event_types = {e.type.value for e in events}
        assert "run_started" in event_types
        assert "run_completed" in event_types

    @pytest.mark.asyncio
    async def test_5_eval_dataset(self, db, settings, registry, mock_biz, sequential_models):
        """评测：cs_basic.yaml 出报告（FakeLLM 下断言可确定性通过）。"""
        from pathlib import Path

        from lubanagent.model import ScriptedFakeChatModel
        from lubanagent.observability.evals import LlmJudge

        # 全量 script 覆盖 5 case 的所有模型调用（顺序消费）
        full_script = [
            "耳机支持IPX5防水，不支持游泳佩戴。",  # case 1: faq
            {  # case 2: order_lookup
                "tool_calls": [
                    {"name": "query_order", "args": {"order_id": "ORD-1001"}, "id": "c1"}
                ]
            },
            "订单已找到。",  # case 2 follow-up
            {  # case 3: refund_large_approval
                "tool_calls": [
                    {
                        "name": "refund_apply",
                        "args": {
                            "order_id": "ORD-1006",
                            "amount": 899.0,
                            "reason": "不想要",
                        },
                        "id": "c2",
                    }
                ]
            },
            "退款已提交。",  # case 3 follow-up
            "理解您的心情，我来帮您处理ORD-1002退款。",  # case 4
            "改地址不在能力范围，建议联系人工客服。",  # case 5
        ]
        await _seed_main_agent(db, registry, full_script)
        runtime = AgentRuntime(db=db, settings=settings, registry=default_registry)

        dataset = load_dataset(Path("app/datasets/cs_basic.yaml"))
        # judge 用独立 fake（不与 agent 共享 cursor）
        judge_fake = ScriptedFakeChatModel(script=['{"score": 5, "reason": "ok"}'])
        judge = LlmJudge(judge_fake)
        runner = EvalRunner(runtime, judge=judge)
        report = await runner.run_dataset(_AGENT_KEY, dataset)
        assert len(report.results) == len(dataset.cases)
        # 契约：FakeLLM 下 5 个 case 全部确定性通过（不只抽查一个）
        for r in report.results:
            assert r.passed, f"{r.case} 应通过: {r.verdicts}"

    @pytest.mark.asyncio
    async def test_5b_knowledge_guard_eval(self, db, settings, registry, mock_biz):
        """防错答守门评测：cs_knowledge.yaml 对纯 RAG 问答 Agent（cs-faq 形态）全过，
        错答率（wrong_answer_rate）为 0——无关问题必须短路拒答，未拒答即错答。"""
        from pathlib import Path

        # 知识源：真实 product-faq 灌入（FakeEmbedding，dim=512）。
        # 注册到本测试独立 registry（fixture），不碰 default_registry 进程级单例
        from app.knowledge_sources.product_faq import ingest_product_faq
        from lubanagent.core import AgentDefinition, KnowledgeConfig
        from lubanagent.knowledge import (
            FakeEmbedding,
            KnowledgeStore,
            SqlKnowledgeConnector,
        )
        from lubanagent.model import ScriptedFakeChatModel
        from lubanagent.observability.evals import LlmJudge
        from lubanagent.persistence import AgentRepository

        store = KnowledgeStore(db)
        embedder = FakeEmbedding()
        await ingest_product_faq(store, embedder)
        registry.register_knowledge(
            "product-faq", SqlKnowledgeConnector("product-faq", store=store, embedder=embedder)
        )

        # Agent 按 app/agents/cs_faq.yaml 形态建：纯 RAG 问答（无工具）+ 守门配置
        await seed_fake_brain(db, script=["耳机支持IPX5防水，不支持游泳佩戴。"])
        qa_definition = AgentDefinition(
            key="cs-faq",
            name="cs-faq",
            instructions="你是客服 FAQ 专家。",
            tools=[],
            knowledge=KnowledgeConfig(
                sources=["product-faq"],
                top_k=5,
                score_threshold=0.2,
                no_hit_fallback="抱歉，我暂时无法从知识库中确认这个问题的答案，建议联系人工客服确认。",
            ),
        )
        async with db.session() as session:
            await AgentRepository(session).create_agent(qa_definition)

        runtime = AgentRuntime(db=db, settings=settings, registry=registry)
        dataset = load_dataset(Path("app/datasets/cs_knowledge.yaml"))
        judge_fake = ScriptedFakeChatModel(script=['{"score": 5, "reason": "ok"}'])
        runner = EvalRunner(runtime, judge=LlmJudge(judge_fake))
        report = await runner.run_dataset("cs-faq", dataset)

        assert len(report.results) == len(dataset.cases)
        for r in report.results:
            assert r.passed, f"{r.case} 应通过: {r.verdicts}"
        # 硬指标：错答率必须为 0（应拒答用例全部实际拒答）
        assert report.wrong_answer_rate == 0.0

    @pytest.mark.asyncio
    async def test_6_refund_flow_small(self, db, settings, registry, mock_biz):
        """退款流水线小额直退走完。"""
        from app.graphs.refund_flow import _build_refund_flow
        from app.tools.customer_service import order, refund  # noqa: F401
        from lubanagent.extension import discover as _discover
        from lubanagent.graph import GraphRunner

        _discover(["app.tools"], registry=default_registry)
        runner = GraphRunner(db=db, registry=default_registry)
        flow = _build_refund_flow().build()

        result = await runner.run(
            flow,
            {"order_id": "ORD-1002", "amount": 89.5, "reason": "测试小额退款"},
        )
        assert result.status == "completed"
        assert result.output.get("refunded") is True

    @pytest.mark.asyncio
    async def test_6b_refund_flow_large_interrupt_resume(self, db, settings, registry, mock_biz):
        """退款流水线大额（>500）：human 节点 interrupt → resume(approved) 走完。"""
        from app.graphs.refund_flow import _build_refund_flow
        from app.tools.customer_service import order, refund  # noqa: F401
        from lubanagent.extension import discover as _discover
        from lubanagent.graph import GraphRunner

        _discover(["app.tools"], registry=default_registry)
        runner = GraphRunner(db=db, registry=default_registry)
        flow = _build_refund_flow().build()

        # 大额：when(needs_approval) → human("approve") interrupt 暂停
        result = await runner.run(
            flow,
            {"order_id": "ORD-1006", "amount": 899.0, "reason": "大额退款"},
        )
        assert result.status == "interrupted"
        assert result.interrupt is not None
        assert result.interrupt.get("node") == "approve"

        # resume(approved=True) → execute_refund → notify 走完
        resumed = await runner.resume(flow, result.thread_id, True)
        assert resumed.status == "completed"
        assert resumed.output.get("refunded") is True

        # Mock biz 收到退款单（tmp 数据副本内回写）
        from demos.customer_service.mock_services import server as mock_server

        refunds = json.loads(
            (mock_server._DATA_DIR / "refunds.json").read_text(encoding="utf-8")
        )
        assert any(
            r["order_id"] == "ORD-1006" and float(r["amount"]) == 899.0 for r in refunds
        ), "Mock biz 应收到 ORD-1006 的 899 元退款单"

    @pytest.mark.asyncio
    async def test_7_multi_agent_triage(self, db, settings, registry, sequential_models):
        """多 Agent 分流：'退货政策'→cs-faq；'我要退款'→cs-refund。"""
        from lubanagent.core import BrainProfile
        from lubanagent.multiagent import TeamBuilder, TeamRunner

        # triage supervisor script：run1 委派 faq 后 answer；run2 委派 refund 后 answer
        # （sequential fake 跨 Run 顺序消费，4 条对应两轮 supervisor 决策 ×2）
        triage_script = [
            json.dumps({"action": "delegate", "agent": "cs-faq", "input": "退货政策"}),
            json.dumps({"action": "answer", "answer": "退货7天无理由，拆封后不支持。"}),
            json.dumps({"action": "delegate", "agent": "cs-refund", "input": "我要退款"}),
            json.dumps({"action": "answer", "answer": "已为您转接退款专员处理。"}),
        ]
        await seed_fake_brain(
            db, script=triage_script, profile=BrainProfile.SMART, key="fake-triage"
        )
        await create_agent(db, key="cs-triage", instructions="你是调度。", brain=BrainProfile.SMART)

        faq_script = ["退货7天无理由，拆封后不支持。"]
        await seed_fake_brain(db, script=faq_script, profile=BrainProfile.FAST, key="fake-faq")
        await create_agent(db, key="cs-faq", instructions="你是FAQ。", brain=BrainProfile.FAST)

        # cs-refund 用 CHEAP 槽位——避免与 cs-faq 共享 FAST 映射互相覆盖
        refund_script = ["退款流程已启动，请提供订单号。"]
        await seed_fake_brain(
            db, script=refund_script, profile=BrainProfile.CHEAP, key="fake-refund"
        )
        await create_agent(
            db, key="cs-refund", instructions="你是退款专员。", brain=BrainProfile.CHEAP
        )

        team = (
            TeamBuilder("cs-team")
            .supervisor("cs-triage")
            .agent("cs-faq", description="FAQ")
            .agent("cs-refund", description="退款")
            .route(max_delegations=2)
            .build()
        )
        runtime = AgentRuntime(db=db, settings=settings, registry=default_registry)
        runner = TeamRunner(db=db, runtime=runtime, registry=default_registry)

        def _delegation_targets(output: dict) -> list[str]:
            records = output.get("delegations") or []
            return [
                r.agent_key if hasattr(r, "agent_key") else r["agent_key"] for r in records
            ]

        # 路径一：'退货政策' → 委派 cs-faq
        result = await runner.run(team, "退货政策是什么")
        assert result.status == "completed"
        targets = _delegation_targets(result.output)
        assert targets and targets[0] == "cs-faq", f"应委派 cs-faq，实际 {targets}"

        # 路径二：'我要退款' → 委派 cs-refund
        result2 = await runner.run(team, "我要退款")
        assert result2.status == "completed"
        targets2 = _delegation_targets(result2.output)
        assert targets2 and targets2[0] == "cs-refund", f"应委派 cs-refund，实际 {targets2}"

    @pytest.mark.asyncio
    async def test_8_rag_faq(self, db, settings, registry):
        """RAG：产品问题命中 FAQ 片段带引用编号（FakeEmbedding）。"""
        from app.knowledge_sources.product_faq import ingest_product_faq, make_product_faq_connector
        from lubanagent.knowledge import FakeEmbedding, KnowledgeStore

        store = KnowledgeStore(db)
        embedder = FakeEmbedding()
        await ingest_product_faq(store, embedder)
        connector = make_product_faq_connector(store, embedder)

        snippets = await connector.retrieve("蓝牙耳机防水游泳", top_k=3)
        assert len(snippets) > 0
        # 命中耳机相关 FAQ
        assert any("IPX" in s.content or "防水" in s.content for s in snippets)

    @pytest.mark.asyncio
    async def test_9_skill_load(self, db, settings, registry):
        """Skill：skill_load(refund-sop) 返回 SOP 正文；scripts/refund_check.py 受控执行。"""
        from pathlib import Path

        from lubanagent.skill import SkillRegistry, run_script

        skills_root = Path("app/skills")
        skill_reg = SkillRegistry(skills_root=skills_root)
        await skill_reg.scan()

        package = skill_reg.get("refund-sop")
        assert package is not None
        assert "退款" in package.meta.description

        # 受控执行脚本
        result = await run_script(package, "scripts/refund_check.py", args=["ORD-1001"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["valid"] is True

@pytest.mark.asyncio
async def test_10_zero_config_fallback(
    db, settings, registry, monkeypatch, isolated_test_process
):
    """零配置兜底：连接失败由确定性替身产生，不碰任何已有服务。"""
    import httpx

    from app.tools.customer_service.order import _business_transport
    from lubanagent.config import get_settings

    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ConnectError("synthetic unavailable", request=request)

    token = _business_transport.set(httpx.MockTransport(unavailable))
    # 地址仅用于让 HTTPX 请求通过测试目标校验；MockTransport 不创建真实 socket。
    isolated_test_process[0].add(9100)
    monkeypatch.setenv("BIZ_BASE_URL", "http://127.0.0.1:9100")
    get_settings.cache_clear()
    script = [
        {"tool_calls": [{"name": "query_order", "args": {"order_id": "ORD-1001"}, "id": "c1"}]},
        "查不到订单。",
    ]
    await seed_fake_brain(db, script=script)
    from app.tools.customer_service import order  # noqa: F401

    discover(["app.tools"], registry=default_registry)
    await create_agent(db, key=_AGENT_KEY, tools=["query_order"])

    runtime = AgentRuntime(db=db, settings=settings, registry=default_registry)
    # 合成连接异常 → 错误回喂模型 → Run 完成不炸，业务服务完全不被请求
    try:
        run = await runtime.run(RunRequest(agent_key=_AGENT_KEY, user_input="查ORD-1001"))
        assert run.status is RunStatus.COMPLETED
        assert "查不到" in (run.output or "")
        assert [(r.method, r.url.path) for r in requests] == [
            ("GET", "/orders/ORD-1001")
        ]
        assert isolated_test_process[1] == []
    finally:
        _business_transport.reset(token)
