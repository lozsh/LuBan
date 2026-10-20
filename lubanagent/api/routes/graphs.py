"""graphs 路由：图只读结构视图（列表 + 拓扑详情；编辑走代码，不做图设计器）。"""

from fastapi import APIRouter

from lubanagent.api.deps import CtxDep
from lubanagent.api.schemas import (
    GraphCondEdgeOut,
    GraphDetailOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphSummaryOut,
)
from lubanagent.core import ErrorCode, LubanError

router = APIRouter(tags=["graphs"])


def _read_structure(built: object):
    """鸭子判定 DSL 构建器并读结构快照；非 DSL（无 structure 方法）返回 None。

    不 isinstance GraphBuilder——api 层禁止 import 编排模块（import-linter 分层契约），
    「暴露 callable structure()」与「是 GraphBuilder」在此语义等价（非 DSL builder 不会带）。
    """
    structure = getattr(built, "structure", None)
    if not callable(structure):
        return None
    return structure()


@router.get("/graphs", response_model=list[GraphSummaryOut])
async def list_graphs(ctx: CtxDep) -> list[GraphSummaryOut]:
    return [
        GraphSummaryOut(
            name=d.name,
            description=d.description,
            structure_available=_read_structure(d.builder()) is not None,
        )
        for d in ctx.registry.list_graphs()
    ]


@router.get("/graphs/{name}", response_model=GraphDetailOut)
async def graph_detail(name: str, ctx: CtxDep) -> GraphDetailOut:
    try:
        descriptor = ctx.registry.get_graph(name)
    except LubanError as exc:
        # registry 未注册抛 VALIDATION_ERROR(422)；对外统一为资源不存在 404。
        # 按 code 收窄：registry 将来抛其他码（如 CONFIG_ERROR）不被误转 404
        if exc.code != ErrorCode.VALIDATION_ERROR:
            raise
        raise LubanError(
            ErrorCode.GRAPH_NOT_FOUND, f"图未注册: {name}", details={"name": name}
        ) from None
    built = descriptor.builder()
    structure = _read_structure(built)
    if structure is None:
        raise LubanError(
            ErrorCode.GRAPH_STRUCTURE_UNAVAILABLE,
            f"图 {name} 的 builder 未返回 DSL 构建器，无可视化结构",
            details={"name": name},
        )
    return GraphDetailOut(
        name=structure.name,
        description=structure.description,
        entry=structure.entry,
        nodes=[
            GraphNodeOut(
                name=n.name,
                kind=n.kind,
                agent_key=n.agent_key,
                input_from=n.input_from,
                output_to=n.output_to,
                prompt=n.prompt,
                summary=n.summary,
            )
            for n in structure.nodes
        ],
        edges=[
            GraphEdgeOut(from_node=e.from_node, to_node=e.to_node)
            for e in structure.edges
        ],
        cond_edges=[
            GraphCondEdgeOut(
                from_node=c.from_node, then_node=c.then_node, otherwise_node=c.otherwise_node
            )
            for c in structure.cond_edges
        ],
    )
