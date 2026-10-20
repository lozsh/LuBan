"""图编排 DSL：.step/.agent/.human/.when/.entry/.edge/.build（契约决策 2/5，demo 级全集）。

state 方案：用户态为纯 dict；LangGraph 侧包一层 `{"data": dict}` 单通道 TypedDict
（plain dict schema 在 langgraph 1.x 不累积 state，故包壳），step fn 返回 dict 即 merge。
"""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import GraphDescriptor

StepFn = Callable[[dict[str, Any]], Any]  # state → state 更新（dict merge）或完整 state；async 亦可


class _FlowState(TypedDict):
    data: dict[str, Any]


async def _noop_started(node: str, kind: str) -> None:
    return None


async def _noop_completed(node: str, kind: str, state: dict[str, Any]) -> None:
    return None


def _no_cancel() -> None:
    return None


@dataclass
class NodeHooks:
    """每 Run 注入的节点执行期钩子（Trace/取消/agent 子 Run 都由 runner 提供）。"""

    check_cancelled: Callable[[], None] = _no_cancel
    check_budget: Callable[[], None] = _no_cancel  # 节点边界预算闸（共享 guard check_time）
    on_node_started: Callable[[str, str], Awaitable[None]] = _noop_started
    on_node_completed: Callable[[str, str, dict[str, Any]], Awaitable[None]] = _noop_completed
    # (agent_key, user_input) -> 子 Run 最终文本输出；None = agent 节点不可用
    run_agent: Callable[[str, str], Awaitable[str]] | None = None


@dataclass
class _Node:
    kind: Literal["step", "agent", "human"]
    fn: StepFn | None = None
    agent_key: str | None = None
    input_from: str = "input"
    output_to: str = "agent_output"
    prompt: str = ""


@dataclass
class _CondEdge:
    src: str
    cond: Callable[[dict[str, Any]], bool]
    then: str
    otherwise: str


@dataclass(frozen=True)
class GraphNodeInfo:
    """只读结构视图的节点信息；fn 等可执行体不暴露（docstring 首行摘要除外）。"""

    name: str
    kind: Literal["step", "agent", "human"]
    agent_key: str | None = None
    input_from: str | None = None
    output_to: str | None = None
    prompt: str | None = None
    summary: str | None = None  # step: fn docstring 首行；agent/human 各自字段已可读


def _doc_summary(fn: StepFn | None) -> str | None:
    """取函数 docstring 首行作节点摘要；无 docstring 返回 None。"""
    if fn is None:
        return None
    doc = inspect.getdoc(fn)
    if not doc:
        return None
    return doc.splitlines()[0].strip() or None


@dataclass(frozen=True)
class GraphEdgeInfo:
    from_node: str
    to_node: str


@dataclass(frozen=True)
class GraphCondEdgeInfo:
    from_node: str
    then_node: str
    otherwise_node: str


@dataclass(frozen=True)
class GraphStructure:
    """GraphBuilder 的只读结构快照（API /v1/graphs 的数据来源，不触发编译）。"""

    name: str
    description: str
    entry: str | None
    nodes: list[GraphNodeInfo]
    edges: list[GraphEdgeInfo]
    cond_edges: list[GraphCondEdgeInfo]


class GraphBuilder:
    """确定性流程 DSL 构建器；节点拓扑校验在 build() 一次性完成。"""

    def __init__(self, name: str, *, description: str = "") -> None:
        self.name = name
        self.description = description
        self._nodes: dict[str, _Node] = {}
        self._edges: list[tuple[str, str]] = []
        self._cond_edges: list[_CondEdge] = []
        self._entry: str | None = None

    # ------------------------------------------------------------ DSL 面

    def entry(self, name: str) -> "GraphBuilder":
        self._entry = name
        return self

    def step(self, name: str, fn: StepFn) -> "GraphBuilder":
        self._add_node(name, _Node(kind="step", fn=fn))
        return self

    def agent(
        self,
        name: str,
        agent_key: str,
        *,
        input_from: str = "input",
        output_to: str = "agent_output",
    ) -> "GraphBuilder":
        """节点=Agent：从 state[input_from] 取用户输入跑 Runtime，结果写 state[output_to]。"""
        self._add_node(
            name,
            _Node(kind="agent", agent_key=agent_key, input_from=input_from, output_to=output_to),
        )
        return self

    def human(self, name: str, *, prompt: str = "") -> "GraphBuilder":
        """人工节点：interrupt 暂停，resume 值写回 state[name]。"""
        self._add_node(name, _Node(kind="human", prompt=prompt))
        return self

    def when(
        self,
        src: str,
        cond: Callable[[dict[str, Any]], bool],
        *,
        then: str,
        otherwise: str,
    ) -> "GraphBuilder":
        self._cond_edges.append(_CondEdge(src=src, cond=cond, then=then, otherwise=otherwise))
        return self

    def edge(self, a: str, b: str) -> "GraphBuilder":
        self._edges.append((a, b))
        return self

    # ------------------------------------------------------------ 构建与校验

    @property
    def has_agent_nodes(self) -> bool:
        return any(node.kind == "agent" for node in self._nodes.values())

    def structure(self) -> GraphStructure:
        """只读结构快照（不校验、不编译）；供 API 序列化节点/边/条件分支。"""
        nodes: list[GraphNodeInfo] = []
        for name, node in self._nodes.items():
            nodes.append(
                GraphNodeInfo(
                    name=name,
                    kind=node.kind,
                    agent_key=node.agent_key if node.kind == "agent" else None,
                    input_from=node.input_from if node.kind == "agent" else None,
                    output_to=node.output_to if node.kind == "agent" else None,
                    prompt=node.prompt if node.kind == "human" else None,
                    summary=_doc_summary(node.fn) if node.kind == "step" else None,
                )
            )
        return GraphStructure(
            name=self.name,
            description=self.description,
            entry=self._entry,
            nodes=nodes,
            edges=[GraphEdgeInfo(a, b) for a, b in self._edges],
            cond_edges=[
                GraphCondEdgeInfo(e.src, e.then, e.otherwise) for e in self._cond_edges
            ],
        )

    def build(self) -> "CompiledFlow":
        """校验：entry 存在、边引用的节点存在、无孤立节点，否则 VALIDATION_ERROR。"""
        if not self._nodes:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"图 {self.name} 没有任何节点")
        if self._entry is None:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"图 {self.name} 未设置 entry")
        if self._entry not in self._nodes:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"图 {self.name} entry 引用了不存在的节点: {self._entry}",
            )
        cond_srcs = {edge.src for edge in self._cond_edges}
        for a, b in self._edges:
            for endpoint in (a, b):
                if endpoint not in self._nodes:
                    raise LubanError(
                        ErrorCode.VALIDATION_ERROR,
                        f"图 {self.name} 边引用了不存在的节点: {endpoint}",
                    )
            if a in cond_srcs:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"图 {self.name} 节点 {a} 同时有普通边与条件边",
                )
        for edge in self._cond_edges:
            for endpoint in (edge.src, edge.then, edge.otherwise):
                if endpoint not in self._nodes:
                    raise LubanError(
                        ErrorCode.VALIDATION_ERROR,
                        f"图 {self.name} 条件边引用了不存在的节点: {endpoint}",
                    )
        reachable = self._reachable()
        orphans = sorted(set(self._nodes) - reachable)
        if orphans:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"图 {self.name} 存在孤立节点: {', '.join(orphans)}",
                details={"orphans": orphans},
            )
        return CompiledFlow(name=self.name, description=self.description, builder=self)

    def __call__(
        self,
        *,
        hooks: NodeHooks | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> CompiledStateGraph:
        """编译为 LangGraph 可执行图（GraphDescriptor.builder 语义）。"""
        return self._compile(hooks=hooks, checkpointer=checkpointer)

    # ------------------------------------------------------------ 内部

    def _add_node(self, name: str, node: _Node) -> None:
        if name in self._nodes:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR, f"图 {self.name} 节点重复定义: {name}"
            )
        self._nodes[name] = node

    def _reachable(self) -> set[str]:
        adjacency: dict[str, set[str]] = {name: set() for name in self._nodes}
        for a, b in self._edges:
            adjacency[a].add(b)
        for edge in self._cond_edges:
            adjacency[edge.src].update({edge.then, edge.otherwise})
        seen: set[str] = set()
        queue = [self._entry]
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(adjacency.get(current, ()))
        return seen

    def _compile(
        self,
        *,
        hooks: NodeHooks | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> CompiledStateGraph:
        hooks = hooks or NodeHooks()
        graph = StateGraph(_FlowState)
        for name, node in self._nodes.items():
            graph.add_node(name, self._wrap(name, node, hooks))
        graph.add_edge(START, self._entry)
        cond_srcs = {edge.src for edge in self._cond_edges}
        for edge in self._cond_edges:

            def router(state: _FlowState, _edge: _CondEdge = edge) -> str:
                return _edge.then if _edge.cond(state["data"]) else _edge.otherwise

            graph.add_conditional_edges(
                edge.src, router, {edge.then: edge.then, edge.otherwise: edge.otherwise}
            )
        for a, b in self._edges:
            graph.add_edge(a, b)
        # 汇点自动接 END（demo 级 DSL，省 .edge(x, END) 样板）
        targets = {a for a, _ in self._edges}
        for name in self._nodes:
            if name not in targets and name not in cond_srcs:
                graph.add_edge(name, END)
        return graph.compile(checkpointer=checkpointer)

    def _wrap(self, name: str, node: _Node, hooks: NodeHooks) -> Callable[..., Awaitable[dict]]:
        if node.kind == "step":
            fn = node.fn

            async def step_node(state: _FlowState) -> dict[str, Any]:
                hooks.check_cancelled()
                hooks.check_budget()
                await hooks.on_node_started(name, "step")
                result = fn(state["data"])
                if inspect.isawaitable(result):
                    result = await result
                data = dict(state["data"]) if result is None else {**state["data"], **result}
                await hooks.on_node_completed(name, "step", data)
                return {"data": data}

            return step_node
        if node.kind == "agent":
            agent_key = node.agent_key
            input_from = node.input_from
            output_to = node.output_to

            async def agent_node(state: _FlowState) -> dict[str, Any]:
                hooks.check_cancelled()
                hooks.check_budget()
                if hooks.run_agent is None:
                    raise LubanError(
                        ErrorCode.CONFIG_ERROR,
                        f"图 {self.name} 的 agent 节点 {name} 需要 GraphRunner 注入 runtime",
                        details={"node": name, "agent_key": agent_key},
                    )
                await hooks.on_node_started(name, "agent")
                user_input = str(state["data"].get(input_from, ""))
                output = await hooks.run_agent(agent_key, user_input)
                data = {**state["data"], output_to: output}
                await hooks.on_node_completed(name, "agent", data)
                return {"data": data}

            return agent_node

        prompt = node.prompt

        async def human_node(state: _FlowState) -> dict[str, Any]:
            hooks.check_cancelled()
            hooks.check_budget()
            await hooks.on_node_started(name, "human")
            value = interrupt({"node": name, "prompt": prompt, "state": dict(state["data"])})
            data = {**state["data"], name: value}
            await hooks.on_node_completed(name, "human", data)
            return {"data": data}

        return human_node


@dataclass(frozen=True)
class CompiledFlow:
    """build() 产物：DSL 定义 + 拓扑校验通过；编译在 GraphRunner 每 Run 进行。"""

    name: str
    description: str
    builder: GraphBuilder = field(compare=False)

    @property
    def has_agent_nodes(self) -> bool:
        return self.builder.has_agent_nodes

    def to_graph_descriptor(self) -> GraphDescriptor:
        """供 Registry/@graph 注册；builder 即 GraphBuilder（可调用，返回编译图）。"""
        return GraphDescriptor(name=self.name, description=self.description, builder=self.builder)


__all__ = ["CompiledFlow", "GraphBuilder", "NodeHooks", "StepFn"]
