# 契约 D1-4：`lubanagent/runtime` Agent Runtime（prebuilt agent + 治理包装）

> 来源：开发计划 D1-4、技术方案 §8/§10.5/§10.6/§13.6、架构设计 §3.3、AGENTS.md §4（一个引擎两种用法）。
> 前置：D0-1/D0-2/D0-3/D1-1/D1-2 全部；D1-5（ToolExecutor/ApprovalManager）、D1-7（TraceWriter/SafeBackend/resolve_backend）同批先行完成。

## 范围

`lubanagent/runtime/`：`agent_loop.py`、`context_builder.py`、`budget_guard.py`、`sse.py`、`cancellation.py`、`__init__.py` + `tests/lubanagent/runtime/`。

## 契约决策

1. **prebuilt agent 用 `langgraph.prebuilt.create_react_agent`**（venv 已装 langgraph；不引完整 `langchain` 包——技术方案 §10.5 的 `create_agent` 语义即 LangGraph prebuilt agent，循环不重写）。
2. **步数边界经 LangGraph `recursion_limit` 强制**：`recursion_limit = max_steps * 2 + 1`（每步 ≈ agent 节点 + tools 节点）；`GraphRecursionError` → `end_reason=STEP_LIMIT`（代码强制）。
3. **会话历史来源**：v1 由调用方传入 `history: list[BaseMessage]`（API 层 D1-3 先传空/由请求体携带）；DB 支撑的会话记忆属 D3-2，不在本单元。
4. **`max_cost` 预算**：价格取自 `ModelDefinition.params` 的 `input_price_per_1k`/`output_price_per_1k`（元/1k tokens）；无价格信息则跳过成本熔断并在 Trace 写 `BUDGET_WARNING`（payload reason="no_pricing"）——Token 估算本就是近似（F7 不过度承诺）。
5. **curl 端到端 SSE 验收归 D1-3**（路由层）；本单元验收以 `stream()` 事件序列断言为准。

## 接口契约

### cancellation.py
```python
class CancellationToken:
    def cancel(self) -> None
    @property
    def cancelled(self) -> bool
    def throw_if_cancelled(self) -> None      # 已取消 → raise RunCancelledError("run cancelled")

class CancellationRegistry:
    def create(self, run_id: str) -> CancellationToken   # 重复 run_id → 返回既有 token
    def cancel(self, run_id: str) -> bool                # 不存在 → False
    def discard(self, run_id: str) -> None
```

### budget_guard.py
```python
class BudgetGuard:
    """运行预算三道闸门：token / cost / 时长（撞线即抛，代码强制）。"""
    def __init__(self, budget: Budget | None, *, timeout_seconds: float,
                 pricing: dict[str, float] | None = None) -> None
        # deadline = monotonic + min(timeout_seconds, budget.max_seconds 若给)
    def check_time(self) -> None            # 过 deadline → RunTimeoutError
    def consume(self, *, input_tokens: int, output_tokens: int) -> None
        # 累计 usage；估算 cost（有 pricing 时）；max_tokens 超 → BudgetExceededError；max_cost 超 → BudgetExceededError
    @property
    def usage(self) -> TokenUsage
    @property
    def cost(self) -> float
    @property
    def pricing_missing(self) -> bool       # 有 max_cost 但无 pricing → True（调用方写 BUDGET_WARNING）
```

### context_builder.py
```python
class ContextBuilder:
    """KV-cache 友好组装（尽力而为非硬不变量，技术方案 §13.6）：稳定前缀 + append-only + 动态后置。"""
    def build_system_prompt(self, definition: AgentDefinition, *,
                            variables: dict[str, str] | None = None) -> str
        # role 段 + instructions 段；variables 合并 definition.prompt_variables 后用 str.format_map 渲染
        # （未提供的变量保留原样 {name}，不报错——SafeDict 语义）
    def build_messages(self, *, history: list[BaseMessage] | None,
                       user_input: str, window_size: int) -> list[BaseMessage]
        # history 截尾 window_size 条（保最新，截最旧）+ 追加 HumanMessage(user_input)；append-only
```

### sse.py
```python
SSE_EVENT_MAP: dict[TraceEventType, str] = {
    RUN_STARTED: "run.started", MODEL_TOKEN_DELTA: "token.delta",
    TOOL_CALL_STARTED: "tool.started", TOOL_CALL_COMPLETED: "tool.finished",
    TOOL_CALL_FAILED: "tool.failed", RUN_COMPLETED: "run.completed",
    RUN_FAILED: "run.failed", RUN_CANCELLED: "run.cancelled",
    TOOL_APPROVAL_REQUESTED: "tool.approval.requested",
    TOOL_APPROVAL_RESOLVED: "tool.approval.resolved",
    MODEL_FALLBACK: "model.fallback", BUDGET_WARNING: "budget.warning",
    BUDGET_EXCEEDED: "budget.exceeded",
    # STEP_STARTED/STEP_COMPLETED/MODEL_CALL_STARTED/MODEL_CALL_COMPLETED 默认不外发（内部 Trace 粒度）
}
def format_sse(event: str, data: dict[str, Any]) -> str
    # "event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
async def sse_stream(events: AsyncIterator[TraceEvent]) -> AsyncIterator[str]
    # 只外发 SSE_EVENT_MAP 覆盖的类型；data = {"run_id":..., "type":..., **event.payload}
```

### agent_loop.py
```python
@dataclass(frozen=True)
class RunRequest:
    agent_key: str
    user_input: str
    session_id: str | None = None
    version: int | None = None            # 缺省 = agents.current_version
    variables: dict[str, str] | None = None
    history: list[BaseMessage] | None = None
    run_id: str | None = None             # 缺省生成 uuid4 hex
    parent_run_id: str | None = None      # 子 Run 契约（§10.6）

class AgentRuntime:
    def __init__(self, *, db: Database, settings: Settings,
                 registry: Registry | None = None,               # 缺省 default_registry
                 observability: ObservabilityBackend | None = None,  # 缺省 resolve_backend(settings, registry)
                 approval_manager: ApprovalManager | None = None,
                 executor: ToolExecutor | None = None) -> None
        # approval_manager 给了则 executor 用其 as_handler(run_id) 接线审批闸门（每 run 一个 handler）

    async def stream(self, request: RunRequest) -> AsyncIterator[TraceEvent]:
        """流式执行：按「行为契约」流程推进，逐步 yield core TraceEvent。终态必出
        RUN_COMPLETED / RUN_FAILED / RUN_CANCELLED 之一（代码强制）。"""

    async def run(self, request: RunRequest) -> Run:
        """非流式：消费 stream 后从 DB 读回终态 Run。"""

    async def cancel(self, run_id: str) -> bool: ...
```

## 行为契约（stream 流程，顺序即契约，全部代码强制）

1. **装载定义**：`version` 指定 → `AgentRepository.get_version`；缺省 → `get_agent`（当前版）。不存在 → 抛 `LubanError(AGENT_NOT_FOUND / AGENT_VERSION_NOT_FOUND)`。
2. **版本可运行性校验**（技术方案 §5.2）：definition.tools 中每个 key 须在 Registry 存在，且 `definition.tool_signatures[key]`（若有记录）与 Registry 内 descriptor.signature_hash 一致；被删/漂移 → 抛 `LubanError(AGENT_VERSION_UNRUNNABLE, details={"tool": key})`。
3. **大脑解析**：`resolve_brain_for_agent`（D1-2）；`used_fallback` → yield 其 `fallback_events`（回填 run_id 后）；`CapabilityMismatchError` → RUN_FAILED（end_reason=error）。
4. **Run 建档**：`TraceWriter.start_run`（status=RUNNING、brain_profile、model_key=resolution.definition.key）→ yield `RUN_STARTED`；`observability.start_trace`（经 SafeBackend 包装）；hooks `pre_run`（逐个 await，异常只记日志不阻断）。
5. **治理起表**：BudgetGuard(...) + CancellationRegistry.create(run_id)；`pricing_missing` → yield `BUDGET_WARNING`（no_pricing）。
6. **上下文**：ContextBuilder 渲染 system prompt + messages；模型 = `create_chat_model(resolution.definition)`。
7. **工具桥接**：每个 agent.tools key → `registry.get_tool` → langchain `StructuredTool`（`func=None, coroutine=bridge`；args_schema 用 args_schema 生成的 pydantic 模型；name=key、description=descriptor.description）。bridge(args)：
   - yield 侧效果：先发 `TOOL_CALL_STARTED`（payload tool/args）→ hooks `pre_tool` → `executor.execute(registered, args, ctx=run_context)` → 成功发 `TOOL_CALL_COMPLETED`（payload 含 result）返回 JSON 字符串；失败发 `TOOL_CALL_FAILED` 并把 error dict 作为工具输出返回模型（治理错误不炸循环，模型可解释失败）；
   - **审批**：executor 闸门经 `approval_manager.as_handler(run_id)` 等待期间——发 `TOOL_APPROVAL_REQUESTED`、Run 状态 UPDATE 为 `WAITING_APPROVAL`（RunRepository）；决定后恢复 RUNNING 并发 `TOOL_APPROVAL_RESOLVED`。
8. **循环执行**：`create_react_agent(model, tools)`；`agent.astream_events(..., config={"recursion_limit": ...}, version="v2")` 逐事件：
   - `on_chat_model_stream` → yield `MODEL_TOKEN_DELTA`（payload {"text": chunk}）；
   - `on_chat_model_end` → 从 usage_metadata 取 token → `guard.consume`（撞预算 → 终止，见 9）→ 累计 model_calls；
   - 每个事件循环点：`token.throw_if_cancelled()` + `guard.check_time()`（代码强制，取消/超时即刻生效）。
9. **边界命中**（终止映射，各写 end_reason 并 yield 对应终态事件）：
   - `GraphRecursionError` → `STEP_LIMIT`；`BudgetExceededError` → `BUDGET_EXCEEDED`（另 yield `BUDGET_EXCEEDED` 事件）；`RunTimeoutError` → `TIMEOUT`；`RunCancelledError` → `CANCELLED`（终态事件 `RUN_CANCELLED`）；其他异常 → `ERROR`（`RUN_FAILED`，error=LubanError.to_dict()["error"]，非 LubanError 包 INTERNAL_ERROR）。
10. **收尾**（任何终态都执行）：Run 更新终态字段（status/output/end_reason/usage/cost/error/ended_at）→ `TraceWriter.finish_run` → yield 终态事件 → `observability.end_trace(status, summary={"usage":..., "cost":..., "end_reason":...})` → `SafeBackend.drain()` → `CancellationRegistry.discard`；hooks `post_step` 按步调用（每完成一个模型/工具步）。
11. **终态事件唯一**：stream 全过程恰好一个 RUN_COMPLETED/RUN_FAILED/RUN_CANCELLED（代码强制，finally 兜底——流程异常出圈也要补 RUN_FAILED）。

## 边界契约

- runtime 可 import core/extension/config/model/persistence/tool/observability + langchain_core/langgraph/anyio；禁止 import api/app.*/mcp/skill/knowledge/memory/graph/multiagent。
- 循环由 prebuilt agent 承担，**不得重写模型↔工具循环**（AGENTS.md §4）；治理只在包装层。
- 步数/预算/超时三道硬边界由代码强制（开发计划 D1-4 关键架构）。
- 子 Run 契约（§10.6）：request.parent_run_id 透传进 Run 行；预算继承/取消传播的完整实现属 D3-4/D3-5，本单元仅在 RunContext/Run 建档透传 parent_run_id（TODO(D3-4) 标注）。

## 验收标准（须实跑，全部用 ScriptedFakeChatModel，零网络）

1. `pytest tests/lubanagent/runtime/` 全绿：
   - **SSE 事件流**：script = [tool_calls, 文本] 的 FakeLLM → `sse_stream(stream(req))` 事件序列含 `run.started → token.delta → tool.started → tool.finished → run.completed`（顺序断言）；
   - **预算熔断**：budget.max_tokens 极小 → 终态 `budget.exceeded` + DB Run.end_reason=BUDGET_EXCEEDED + TraceWriter 落库完整；
   - **步数**：max_steps=1 且 script 连续 tool_calls → end_reason=STEP_LIMIT；
   - **超时**：timeout_seconds 极小 + 慢工具 → end_reason=TIMEOUT；
   - **取消**：执行中 `runtime.cancel(run_id)` → 终态 `run.cancelled` + DB 状态 CANCELLED；
   - **能力校验**：agent 绑 tool + 大脑 tool_calling=False + 无 fallback → RUN_FAILED(error=CAPABILITY_MISMATCH)；有 fallback → MODEL_FALLBACK 事件先于 RUN_COMPLETED；
   - **审批**：approval_required 工具 + ApprovalManager 接线 → 出现 `tool.approval.requested` 且 Run 状态一度 WAITING_APPROVAL，decide(True) 后 `tool.approval.resolved` 并走完；
   - **版本可运行性**：Registry 删除 agent 引用的 tool 后发起 → AGENT_VERSION_UNRUNNABLE；
   - **Trace 七问**：一次完整 Run 后，从 DB 读回 runs/traces/trace_events/token_usages 回答七问；
   - **观测双写**：stub 后端断言 start_trace/emit/end_trace 调用序列；内层爆炸不阻断 Run 完成。
2. 全量回归绿；`ruff check lubanagent/runtime` 无错。

## 存疑项与决策

1. prebuilt 选型与 recursion_limit 步数语义 → 契约决策 1/2。
2. 工具失败回喂模型 vs 终止 Run → 决策：回喂（标准 Agent 语义，模型可解释失败；治理类终止走边界映射）。
3. 会话历史持久化归 D3-2；预算/取消跨执行器传播归 D3-4/D3-5（本单元 TODO 标注透传点）。
