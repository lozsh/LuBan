"""graphs 路由测试：列表/详情/404/结构不可用。"""

import pytest

from lubanagent.extension import Registry
from lubanagent.extension.protocols import GraphDescriptor
from lubanagent.graph import GraphBuilder


def _demo_builder() -> GraphBuilder:
    def _check(state: dict) -> dict:
        """资格校验步骤"""

        return state

    builder = GraphBuilder("demo-flow", description="测试图：校验→审批→执行")
    builder.entry("check")
    builder.step("check", _check)
    builder.human("approve", prompt="需人工审批")
    builder.agent("act", agent_key="demo-agent")
    builder.edge("check", "approve")
    builder.edge("approve", "act")
    builder.when("check", lambda s: True, then="approve", otherwise="act")
    return builder


def _register(registry: Registry, name: str, builder_fn) -> None:
    registry.register_graph(
        GraphDescriptor(name=name, description=f"图 {name}", builder=builder_fn)
    )


@pytest.mark.asyncio
async def test_list_graphs(client, registry: Registry):
    _register(registry, "demo-flow", _demo_builder)
    _register(registry, "raw-graph", lambda: object())  # 非 DSL 构建

    resp = await client.get("/v1/graphs")
    assert resp.status_code == 200
    data = {item["name"]: item for item in resp.json()}
    assert data["demo-flow"]["structure_available"] is True
    assert data["demo-flow"]["description"] == "图 demo-flow"
    assert data["raw-graph"]["structure_available"] is False


@pytest.mark.asyncio
async def test_graph_detail_fields(client, registry: Registry):
    _register(registry, "demo-flow", _demo_builder)

    resp = await client.get("/v1/graphs/demo-flow")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "demo-flow"
    assert body["entry"] == "check"
    nodes = {n["name"]: n for n in body["nodes"]}
    assert set(nodes) == {"check", "approve", "act"}
    assert nodes["approve"]["kind"] == "human"
    assert nodes["approve"]["prompt"] == "需人工审批"
    assert nodes["act"]["kind"] == "agent"
    assert nodes["act"]["agent_key"] == "demo-agent"
    assert nodes["act"]["input_from"] == "input"
    assert nodes["check"]["kind"] == "step"
    assert nodes["check"]["summary"] == "资格校验步骤"
    assert {(e["from_node"], e["to_node"]) for e in body["edges"]} == {
        ("check", "approve"),
        ("approve", "act"),
    }
    assert body["cond_edges"] == [
        {"from_node": "check", "then_node": "approve", "otherwise_node": "act"}
    ]


@pytest.mark.asyncio
async def test_graph_not_found(client):
    resp = await client.get("/v1/graphs/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "GRAPH_NOT_FOUND"


@pytest.mark.asyncio
async def test_graph_structure_unavailable(client, registry: Registry):
    _register(registry, "raw-graph", lambda: object())

    resp = await client.get("/v1/graphs/raw-graph")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "GRAPH_STRUCTURE_UNAVAILABLE"


def test_structure_accessor_no_compile():
    """structure() 直接读 DSL 状态：不校验（未设 entry 也能读）、不触发编译。"""
    builder = GraphBuilder("partial", description="未完成图")
    builder.step("only", lambda s: s)
    structure = builder.structure()
    assert structure.entry is None
    assert [n.name for n in structure.nodes] == ["only"]
    assert structure.edges == []
    assert structure.cond_edges == []
