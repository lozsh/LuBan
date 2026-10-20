"""flow_as_tool：注册进 Registry → ToolExecutor 执行 → flow output；interrupted 语义返回。"""

import asyncio

from lubanagent.core import (
    AgentDefinition,
    BoundaryConfig,
    Budget,
    EndReason,
    RiskLevel,
    RunStatus,
    TraceEventType,
)
from lubanagent.extension import Registry
from lubanagent.graph import GraphBuilder, flow_as_tool
from lubanagent.observability.cost import build_run_tree_cost_report
from lubanagent.persistence import AgentRepository, RunRepository
from lubanagent.runtime import RunRequest
from lubanagent.tool import ToolExecutor

from .conftest import UsageFakeChatModel, seed_fake_brain


def _echo_flow():
    return (
        GraphBuilder("echo-flow", description="回显流程")
        .step("upper", lambda s: {"echo": str(s.get("input", "")).upper()})
        .entry("upper")
        .build()
    )


async def test_flow_as_tool_completed(make_runner, registry: Registry):
    runner = make_runner()
    registered = flow_as_tool(_echo_flow(), runner)
    assert registered.descriptor.risk == RiskLevel.MEDIUM
    assert registered.descriptor.args_schema["additionalProperties"] is True

    registry.register_tool(registered.descriptor, registered.handler)
    result = await ToolExecutor().execute(registry.get_tool("echo-flow"), {"input": "hello"})

    assert result.success, result.error
    assert result.value["echo"] == "HELLO"  # output = 终态 state（含输入键）


async def test_flow_as_tool_interrupted_semantics(make_runner, registry: Registry):
    flow = (
        GraphBuilder("approval-flow")
        .human("approve", prompt="请审批")
        .step("done", lambda s: {"done": True})
        .entry("approve")
        .edge("approve", "done")
        .build()
    )
    runner = make_runner()
    registered = flow_as_tool(flow, runner, key="approval")
    registry.register_tool(registered.descriptor, registered.handler)

    result = await ToolExecutor().execute(registry.get_tool("approval"), {"input": "x"})

    assert result.success, result.error
    assert result.value["status"] == "interrupted"
    assert result.value["interrupt"]["node"] == "approve"
    assert result.value["thread_id"]  # 供业务侧 resume


async def test_flow_as_tool_budget_inheritance_end_to_end(
    make_runner, make_runtime, db, registry: Registry, monkeypatch
):
    """父 Agent → flow_as_tool → 图内 agent 节点：共享父 guard，整条撞 BUDGET_EXCEEDED。"""
    await seed_fake_brain(db, script=["占位"])
    async with db.session() as session:
        repo = AgentRepository(session)
        await repo.create_agent(
            AgentDefinition(key="child-agent", name="child-agent", instructions="你是子助手。")
        )
    flow = GraphBuilder("refund-flow").agent("ask", "child-agent").entry("ask").build()
    runner = make_runner(runtime=make_runtime())
    registered = flow_as_tool(flow, runner, key="refund_flow")
    registry.register_tool(registered.descriptor, registered.handler)
    async with db.session() as session:
        await AgentRepository(session).create_agent(
            AgentDefinition(
                key="parent-agent",
                name="parent-agent",
                instructions="你是父助手。",
                tools=["refund_flow"],
                tool_signatures={"refund_flow": registered.descriptor.signature_hash},
                # 小预算：父 15 + 子 15 = 30 > 25，父子合计必撞线
                boundary=BoundaryConfig(budget=Budget(max_tokens=25)),
            )
        )
    scripts = [
        [  # 父 Run 一个模型实例：先调 flow 工具，再给终答（终答消费同样撞线）
            {"tool_calls": [{"name": "refund_flow", "args": {"input": "走退款流程"}, "id": "c1"}]},
            "最终答案",
        ],
        ["长回答"],  # 子 Run（图内 agent 节点）一个模型实例
    ]
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=scripts.pop(0), usage=(10, 5)),
    )

    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="parent-agent", user_input="我要退款"))

    # 子 Run 消费进父池撞线 → 图 Run 失败 → 父第二次模型消费也撞线：整条 BUDGET_EXCEEDED
    assert run.status is RunStatus.FAILED
    assert run.end_reason is EndReason.BUDGET_EXCEEDED
    # 父池累计：父 15 + 子 15 + 父第二次 15 = 45（子 Run 烧父池，不允许父无声跑完）
    assert run.token_usage.total == 30  # H11 本地归因：父 Run 只存自己两次调用的消费
    async with db.session() as session:
        graph_runs = await RunRepository(session).list_runs("graph:refund-flow")
        child_runs = await RunRepository(session).list_runs("child-agent")
        tree = await build_run_tree_cost_report(session, run.id)
    assert graph_runs[0].status is RunStatus.FAILED
    assert graph_runs[0].end_reason is EndReason.BUDGET_EXCEEDED
    assert graph_runs[0].parent_run_id == run.id  # parent_run_id 经 _ctx 透传
    assert child_runs[0].status is RunStatus.FAILED
    assert child_runs[0].end_reason is EndReason.BUDGET_EXCEEDED
    # 报表口径：本例未配价（无费用账本行），费用树各项为 0；后代含图编排 Run 与子 agent Run
    assert tree.own_cost == 0.0
    assert tree.total_cost == 0.0
    assert tree.descendant_runs >= 2  # 图编排 Run + 子 agent Run


async def test_flow_as_tool_cancel_propagation_end_to_end(
    make_runner, make_runtime, db, registry: Registry
):
    """父 Run cancel → ctx.metadata token 经 _ctx 进图 → 节点边界感知，图/父 Run 均 CANCELLED。"""
    step_started = asyncio.Event()

    async def slow_step(state):
        step_started.set()  # 图 Run 已建档并进到节点内：此时 cancel 才是确定性的
        await asyncio.sleep(0.3)  # 给 cancel 留窗口，节点 b 边界必须感知
        return {"a": 1}

    flow = (
        GraphBuilder("slow-flow")
        .step("a", slow_step)
        .step("b", lambda s: {"b": 2})
        .entry("a")
        .edge("a", "b")
        .build()
    )
    runner = make_runner()
    registered = flow_as_tool(flow, runner, key="slow_flow")
    registry.register_tool(registered.descriptor, registered.handler)
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "slow_flow", "args": {"input": "x"}, "id": "c1"}]},
            "不会走到",
        ],
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(
            AgentDefinition(
                key="parent-agent",
                name="parent-agent",
                instructions="你是父助手。",
                tools=["slow_flow"],
                tool_signatures={"slow_flow": registered.descriptor.signature_hash},
            )
        )
    runtime = make_runtime()
    request = RunRequest(agent_key="parent-agent", user_input="跑流程", run_id="cancel-parent-1")

    async def cancel_when_step_started() -> None:
        await asyncio.wait_for(step_started.wait(), timeout=5.0)
        assert await runtime.cancel("cancel-parent-1") is True

    cancel_task = asyncio.create_task(cancel_when_step_started())
    events = [event async for event in runtime.stream(request)]
    await cancel_task

    assert events[-1].type is TraceEventType.RUN_CANCELLED
    async with db.session() as session:
        graph_runs = await RunRepository(session).list_runs("graph:slow-flow")
    assert graph_runs[0].status is RunStatus.CANCELLED  # 节点边界 token.throw_if_cancelled
    assert graph_runs[0].parent_run_id == "cancel-parent-1"
