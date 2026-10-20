# 契约 D3-4：`lubanagent/graph` 图编排（确定性流程）

> 来源：开发计划 D3-4、技术方案 §10.5（三种结合形态）/§10.6（子 Run 契约）、架构设计 §2/§3.3、AGENTS.md §4（一个引擎两种用法）。
> 前置：D1-1（checkpoints 表已在 0001）、D1-4（AgentRuntime/RunRequest.parent_run_id/TraceWriter）、D1-5（ToolExecutor）、D0-2（@graph/GraphDescriptor/Registry）。

## 范围

`lubanagent/graph/`：`builder.py`、`checkpoint.py`、`tool_adapter.py`、`runner.py`、`__init__.py` + `tests/lubanagent/graph/`。

## 契约决策

1. **checkpointer 自研 SQLAlchemy Saver**（不引 langgraph PostgresSaver 等外部服务依赖，落 0001 已建的 `checkpoints` 表）：实现 `BaseCheckpointSaver` 的 `get_tuple/put/list/delete_thread` 四方法（LangGraph 版本匹配的必需子集；serde 用 `langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer`）。
2. **DSL 三形态**（技术方案 §10.5）：`.step(name, fn)` 节点=普通 Python（可直接复用 Tool 注册表/模型工厂）、`.agent(name, agent_key)` 节点=Agent（内部跑 D1-4 Runtime 一次，子 Run）、`.human(name)` 人工节点（LangGraph `interrupt`）、`.when(src, cond, then=, else=)` 条件边、`.entry(name)`/`.edge(a, b)`、`.build()`。
3. **编排 Run = 子 Run**：每次图执行在 DB 落一条 Run（`parent_run_id` 透传、`brain_profile` 记触发方 profile 或 "graph"、agent_key 记 `graph:<flow_name>`、`agent_version`=0），节点流转写 TraceEvent（STEP_STARTED/STEP_COMPLETED，payload 含 node/state 摘要）；预算继承/取消传播：**取消**经 CancellationRegistry 查父 token（父取消 → 图内下个节点边界抛 RunCancelledError，代码强制）；**预算继承**经传入父 RunContext 的 budget 共享同一 BudgetGuard 实例（传入则共用、不传则按 graph 自身 budget——TODO(D3-5) 完整治理链）。
4. **人工节点恢复**：`GraphRunner.resume(thread_id, value)` 经 `Command(resume=value)` 驱动；`GraphRunner.run(...)` 返回 `GraphResult(status: "completed"|"interrupted", output, interrupt: dict|None, run_id, thread_id)`；interrupted 时 payload 带人工节点名与提示信息。
5. **demo 级 DSL 面**：`.step/.agent/.human/.when/.entry/.edge/.build` 即全集，不做可视化/复杂 DSL（AGENTS.md 不做低代码设计器）。

## 接口契约

### builder.py
```python
StepFn = Callable[[dict[str, Any]], Any]            # state → state 更新（dict merge）或完整 state；async 亦可
class GraphBuilder:
    def __init__(self, name: str, *, description: str = "") -> None
    def entry(self, name: str) -> "GraphBuilder"
    def step(self, name: str, fn: StepFn) -> "GraphBuilder"
    def agent(self, name: str, agent_key: str, *, input_from: str = "input",
              output_to: str = "agent_output") -> "GraphBuilder"
        # 节点=Agent：从 state[input_from] 取用户输入跑 Runtime，结果写 state[output_to]（子 Run，parent_run_id 透传）
    def human(self, name: str, *, prompt: str = "") -> "GraphBuilder"
        # 节点内 interrupt({"node": name, "prompt": prompt, "state": state 摘要})
    def when(self, src: str, cond: Callable[[dict[str, Any]], bool], *,
             then: str, otherwise: str) -> "GraphBuilder"
    def edge(self, a: str, b: str) -> "GraphBuilder"
    def build(self) -> "CompiledFlow"          # 校验：entry 存在、边引用的节点存在、无孤立节点 → 否则 VALIDATION_ERROR

@dataclass(frozen=True)
class CompiledFlow:
    name: str; description: str; builder: GraphBuilder   # 保留 DSL 定义供 descriptor/导出
    def to_graph_descriptor(self) -> GraphDescriptor     # 供 Registry/@graph 注册
```

### checkpoint.py
```python
class LubanCheckpointer(BaseCheckpointSaver):
    """checkpoints 表落库（自有 DB，不引外部服务）。"""
    def __init__(self, db: Database) -> None
    async def aget_tuple(self, config) -> CheckpointTuple | None
    async def aput(self, config, checkpoint, metadata, new_versions) -> Any
    async def alist(self, config, *, filter=None, before=None, limit=None) -> AsyncIterator[CheckpointTuple]
    async def adelete_thread(self, thread_id: str) -> None
    # 同步 get_tuple/put/list 委托 anyio.run 包装（LangGraph 部分路径调同步版）
```

### runner.py
```python
@dataclass(frozen=True)
class GraphResult:
    status: Literal["completed", "interrupted"]
    output: dict[str, Any]                 # 终态 state（interrupted 时为当前 state）
    interrupt: dict[str, Any] | None       # interrupted 时的 interrupt payload
    run_id: str; thread_id: str

class GraphRunner:
    def __init__(self, *, db: Database, runtime: AgentRuntime | None = None,
                 registry: Registry | None = None,
                 checkpointer: LubanCheckpointer | None = None) -> None
        # runtime 缺省 None → .agent() 节点不可用（构建期校验：含 agent 节点且无 runtime → CONFIG_ERROR）
    async def run(self, flow: CompiledFlow, input: dict[str, Any], *,
                  thread_id: str | None = None,
                  parent_ctx: RunContext | None = None) -> GraphResult
        # thread_id 缺省 uuid4；落子 Run（决策 3）；逐节点 TraceEvent；父 token 取消传播（决策 3）
    async def resume(self, flow: CompiledFlow, thread_id: str, value: Any) -> GraphResult
        # Command(resume=value) 续跑；线程不存在 → RUN_NOT_FOUND
```

### tool_adapter.py（流程 = Tool，关键方向）
```python
def flow_as_tool(flow: CompiledFlow, runner: GraphRunner, *,
                 key: str | None = None, description: str | None = None,
                 risk: RiskLevel = RiskLevel.MEDIUM) -> RegisteredTool
    """CompiledFlow → RegisteredTool：args_schema={"type":"object","additionalProperties":true}
    （input 字段 str）；handler 调 runner.run；interrupted → 返回 {"status":"interrupted","interrupt":...}
    （由上层/模型决定是否推进，恢复经 API/业务侧 resume）；completed → 返回 output。"""
```

### __init__.py
re-export：GraphBuilder/CompiledFlow/LubanCheckpointer/GraphRunner/GraphResult/flow_as_tool。

## 行为契约

1. 退款流水线形态（开发计划验收）能表达并跑通：资格校验（step)→金额计算（step)→when（金额>500→人工 approve，否则 refund)→执行退款（step)→通知（step)；大额在 human 节点 interrupted，`resume(thread_id, {"approved": True})` 后走完（代码强制：集成测试即此流水线）。
2. 人工节点暂停时 checkpoint 已落库（代码强制：interrupted 后直接查 checkpoints 表有记录）。
3. 节点=Agent 产生子 Run（parent_run_id=图 Run 的 run_id，绑各自 agent 版本与模型，§10.6 Trace 上下文）。
4. `flow_as_tool` 产物可被 D1-4 普通 Agent 经 Registry 调用（三种结合形态之三，代码强制）。
5. 每节点流转写 Trace（STEP_STARTED/COMPLETED，含 node 名），可回放。

## 边界契约

- graph 可 import core/extension/config/persistence/model/runtime/tool/observability + langgraph/langchain_core；禁止 import api/app.*/mcp/skill/knowledge/memory/multiagent/fastapi。
- 用户不为普通 Agent 写图；图用于确定性流程（AGENTS.md §4）。

## 验收标准（须实跑，FakeLLM + tmp SQLite）

1. `pytest tests/lubanagent/graph/` 全绿：
   - **退款流水线**（行为契约 1 全链路，含 interrupt 落 checkpoint 与 resume 恢复）；
   - builder 校验：无 entry/边引用不存在节点 → VALIDATION_ERROR；
   - checkpointer：put→get_tuple 往返；list 按 thread 查；delete_thread 清空；
   - agent 节点：fake Agent（scripted）→ 子 Run 落库且 parent_run_id 正确；
   - flow_as_tool：注册进 Registry → ToolExecutor 执行 → 返回 flow output；interrupted 语义返回；
   - 取消传播：父 token 取消 → 下一节点边界 RunCancelledError → 图 Run 终态 CANCELLED；
   - Trace：每节点 STEP_STARTED/COMPLETED 齐全。
2. 全量回归绿；`ruff check lubanagent/graph` 无错。

## 存疑项与决策

1. 预算完整继承链（子 Run 共享 BudgetGuard）→ 本单元经 parent_ctx 传入共用；D3-5 多 Agent 收口完整治理链（契约决策 3）。
2. checkpointer 同步接口 → anyio.run 包装（LangGraph 兼容层；async 路径为主）。
3. `agent_version=0` 与 `agent_key=graph:<flow>` 是图 Run 的标记约定（流程无 Agent 版本概念，Trace 七问对图 Run 以节点事件回答）。
