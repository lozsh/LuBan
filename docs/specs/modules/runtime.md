<!--
TEMPLATE-LOCK: 本文件章节锁定为以下清单，禁止新增/改名/调序：
1. 定位
2. 接口契约
3. 数据模型
4. 行为（状态机/调用树，均无则写"无"）
5. 边界规则
6. 名词
7. 上下游
所有模块（含子域）统一使用本模板。文件名为真实模块名，放在 modules/ 目录下。
-->

# runtime 模块

## 定位

`lubanagent/runtime/` 是 Agent 的执行引擎层：一次 Run = LangGraph prebuilt agent（模型↔工具循环，由 LangGraph 承担）+ 本模块提供的治理包装（步数上限、预算三闸、超时、协作式取消、Trace/SSE 事件流、可选逐步 checkpoint 与崩溃续跑）。不存在独立"聚合根实体"或专属数据库表——本模块不做持久化决策：状态变化写给 `TraceWriter`（`lubanagent/observability`）落库；持有 `checkpointer`（`LubanCheckpointer`，本体属 persistence 模块）时，LangGraph 在每个节点边界把执行快照落 `checkpoints` 表，本模块不直接写该表。核心运行期对象是 `AgentRuntime`（入口）与一次调用期临时对象 `BudgetGuard` / `CancellationToken`（`lubanagent/runtime/agent_loop.py:203`、`lubanagent/runtime/budget_guard.py:8`、`lubanagent/runtime/cancellation.py:8`）。

## 接口契约

### HTTP

本模块不直接暴露 HTTP 路由；HTTP 层由 `lubanagent/api/routes/runs.py` 薄封装本模块的 Python API（见"上下游 · 下游影响"）。

### RPC / MQ / Cron

无。本模块是进程内 Python API，不含 RPC/MQ/Cron 入口。

### Python API（本模块对外的核心契约）

| 类/函数 | 签名要点 | 说明 | 错误情况 |
|---|---|---|---|
| `AgentRuntime.__init__` | `db, settings, registry=None, observability=None, approval_manager=None, executor=None, skill_registry=None, summarizer=None, user_key_fn=None, mcp_registry=None, checkpointer=None` | 组装一次运行期所需的全部依赖（`lubanagent/runtime/agent_loop.py:211-250`）；`checkpointer`（`LubanCheckpointer \| None`）非空时 prebuilt agent 以 `create_react_agent(model, tools, checkpointer=...)` 编译并在 config 带 `configurable.thread_id = f"run-{run_id}"`，每个节点边界由 LangGraph 落 checkpoint（复用 `checkpoints` 表，零迁移）；为 `None` 时零 checkpoint 写入、行为与未启用版完全一致 | 无（纯装配，不做 I/O） |
| `AgentRuntime.stream(request: RunRequest) -> AsyncIterator[TraceEvent]` | 流式执行；终态必出 `RUN_COMPLETED`/`RUN_FAILED`/`RUN_CANCELLED` 之一（`agent_loop.py:292-333`） | 主入口，SSE/图节点/多 Agent 委派均走此方法 | 装载定义失败直接 `raise LubanError`（不产生 Run 记录，见边界规则）；大脑解析 `CapabilityMismatchError` 被捕获转成 `RUN_FAILED` 事件后 `return`（`agent_loop.py:305-315`） |
| `AgentRuntime.stream_resume(run_id: str) -> AsyncIterator[TraceEvent]` | 从最近 checkpoint 续跑非终态 Run；整体在 per-run `asyncio.Lock`（常驻字典，锁与 checkpoint 生命周期同界）内执行；校验与续跑语义见行为章节（`agent_loop.py:361-441`） | 崩溃/中断恢复入口，HTTP `POST /v1/runs/{run_id}/resume` 的直接后端；事件协议与 `stream()` 一致，终态收束共用同一代码路径 | 校验失败在流开始前 `raise LubanError`：run 不存在 → `RUN_NOT_FOUND`；终态（COMPLETED/FAILED/CANCELLED）→ `VALIDATION_ERROR`；本进程活跃（cancellation registry 在册）→ `VALIDATION_ERROR`；子 Run（`parent_run_id` 非空）→ `VALIDATION_ERROR`（生命周期归父编排）；无 checkpoint → `RUN_NOT_FOUND` + `details={"reason": "no_checkpoint"}`；本 Runtime 未注入 checkpointer → `CONFIG_ERROR`（`agent_loop.py:367-407`） |
| `AgentRuntime.run(request: RunRequest) -> Run` | 非流式：内部消费 `stream()` 后从 DB 读回终态 `Run`（`agent_loop.py:335-348`） | 供图节点 (`graph/runner.py`)、多 Agent 委派 (`multiagent/team.py`、`multiagent/supervisor.py`) 同步取子 Run 结果 | `Run` 未落库（启动前失败）时 `raise LubanError(ErrorCode.RUN_NOT_FOUND, ...)`（`agent_loop.py:343-347`） |
| `AgentRuntime.cancel(run_id: str) -> bool` | 委托 `CancellationRegistry.cancel`（`agent_loop.py:350-351`） | 未找到对应 run_id 的 token 返回 `False`，不抛异常；resume 续跑前会重新注册取消 token，续跑中可同样取消 | 无异常路径 |
| `RunRequest`（frozen dataclass） | `agent_key, user_input, session_id=None, user_id=None, version=None, variables=None, history=None, run_id=None, parent_run_id=None, budget_guard=None, instructions_override=None`（`agent_loop.py:131-143`） | Run 请求载体；`parent_run_id`+`budget_guard` 是子 Run 预算继承/编排关联的契约字段（§10.6）；`user_id` 为透传观测标签（无用户管理语义） | 无校验逻辑本身，字段合法性在 `_load_definition`/`_check_runnable` 校验 |
| `BudgetGuard(budget, *, timeout_seconds, pricing=None)` | 三道闸：token / cost / 时长（`budget_guard.py`） | `check_time()`：过 deadline 抛 `RunTimeoutError`；`consume(input_tokens, output_tokens, pricing=_UNSET) -> float`：累计用量+按当次单价计费并返回本次成本（不判定限额；pricing 缺省回落构造期单价，显式 None = 本次调用无单价）；`enforce()`：token/cost 撞线抛 `BudgetExceededError`；`usage`/`cost`（共享池累计值）/`max_cost`/`pricing_missing` 只读属性 | `check_time` → `RunTimeoutError`；`enforce` → `BudgetExceededError`（token 或 cost 任一超限） |
| `CancellationToken` | `cancel()`、`cancelled` 属性、`throw_if_cancelled()`（`cancellation.py:8-23`） | 协作式取消：`threading.Event` 承载标志位，事件循环检查点主动调用 `throw_if_cancelled` | `throw_if_cancelled` → `RunCancelledError`（已取消时） |
| `CancellationRegistry` | `create(run_id) -> CancellationToken`（幂等）、`cancel(run_id) -> bool`、`discard(run_id)`、`is_active(run_id)`（`cancellation.py:26-56`） | run_id 到 token 的注册表，`threading.Lock` 保护；`is_active` 供 `stream_resume` 做"本进程活跃拒绝"检查 | 无 |
| `ContextBuilder.build_system_prompt(definition, *, variables=None) -> str` | role 段 + instructions 段拼接，`variables` 与 `definition.prompt_variables` 合并后按占位符正则渲染：**整个花括号内恰为一个标识符且与已声明变量同名才替换**（R13：原 `format_map` 会把指令里的 JSON 字面量当模板字段炸掉 Run）；未声明占位符与 JSON 字面量原样保留（`context_builder.py:16-33`） | KV-cache 稳定前缀的生产者 | 无异常 |
| `ContextBuilder.build_messages(*, history, user_input, window_size) -> list[BaseMessage]` | `history` 按 `window_size` 截尾（保最新）后追加一条 `HumanMessage`（append-only）（`context_builder.py:27-37`） | 无能力件（记忆/知识/Skill）时的默认消息组装路径 | 无异常 |
| `capabilities.prepare(definition, *, user_input, session_id, user_key, db, registry, skill_registry=None, summarizer=None) -> CapabilityContext` | 按 `definition` 配置逐项装配记忆/知识/Skill，全部默认关闭、显式 early-return（`capabilities.py:82-193`）；知识检索结果经守门过滤（阈值/超龄），过滤后全 miss 且配置 `no_hit_fallback` 时置 `knowledge_no_hit=True` | 供 `agent_loop._execute` 在"上下文组装前"调用 | 知识源未注册 → `raise LubanError(ErrorCode.CONFIG_ERROR, ...)`（`capabilities.py:144-149`，唯一显式抛错路径） |
| `capabilities.finalize(definition, *, session_id, run_id, user_input, output, db, summarizer=None) -> None` | Run 收尾回写会话/摘要记忆（`capabilities.py:196-229`）；知识全 miss 短路时 `output` 为兜底话术，照常作为 assistant 消息回写 | 供 `agent_loop._execute` 在"Run 收尾后"调用 | 内部捕获全部异常仅记日志，不向上抛（`capabilities.py:228-229`） |
| `sse_stream(events: AsyncIterator[TraceEvent]) -> AsyncIterator[str]` | 只外发 `SSE_EVENT_MAP` 覆盖的事件类型，`STEP_*`/`MODEL_CALL_*` 等内部粒度事件默认不外发（`sse.py:10-26`）；外发帧 payload 先经 `redact_value` 脱敏（纵深防御，`sse.py:33-40`） | `api/routes/runs.py` 直接用它包装 `AgentRuntime.stream()` 的输出 | 无异常，`continue` 跳过未映射类型 |
| `format_sse(event: str, data: dict) -> str` | 生成一帧 `event: xxx\ndata: {...}\n\n` 文本（`sse.py:27-28`） | SSE 协议底层拼帧函数 | 无 |

## 数据模型

本模块无专属数据库表，不做持久化。运行期内存数据结构（非 ORM 实体）：

### `RunRequest`（`lubanagent/runtime/agent_loop.py:131-143`，frozen dataclass）

| 字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|
| `agent_key: str` | ✓ | — | 目标 Agent key |
| `user_input: str` | ✓ | — | 本轮用户输入 |
| `session_id: str \| None` | — | `None` | 会话标识，驱动记忆能力件是否启用 |
| `user_id: str \| None` | — | `None` | 透传观测标签（无用户管理语义）；`_execute` 构造 `Run`/`RunContext` 时赋值落库（`agent_loop.py:335,359`），下游图/多 Agent 子 Run 经 `parent_ctx.user_id` 继承 |
| `version: int \| None` | — | `None` | 缺省 = `agents.current_version` |
| `variables: dict[str, str] \| None` | — | `None` | Prompt 变量覆盖 |
| `history: list[BaseMessage] \| None` | — | `None` | 无能力件记忆时使用的历史消息 |
| `run_id: str \| None` | — | `None` | 缺省生成 `uuid4().hex` |
| `parent_run_id: str \| None` | — | `None` | 子 Run 契约字段（技术方案 §10.6） |
| `budget_guard: BudgetGuard \| None` | — | `None` | 子 Run 预算继承：共用父 guard，消费进同一池 |
| `instructions_override: str \| None` | — | `None` | 试跑临时替换 instructions（不入库，版本定义不变） |

说明：`RunRequest.user_id` 在 `_execute` 建档时写入 `Run.user_id` 与 `RunContext.user_id`（`core/run.py:49,75`，均为 `str | None` 默认 `None` 的透传观测标签字段）；`RunContext` 完整字段清单见 core-extension-config 模块文档。

### `BudgetGuard` 内部状态（`budget_guard.py:8-75`，非持久化）

| 字段 | 类型 | 说明 |
|---|---|---|
| `_budget` | `Budget \| None`（`lubanagent.core.budget.Budget`） | 上限来源：`max_tokens`/`max_cost`/`max_seconds` 任一为 `None` 表示不限 |
| `_deadline` | `float`（`time.monotonic()` 基准） | `min(timeout_seconds, budget.max_seconds)` 计算得出 |
| `_pricing` | `dict[str, float] \| None` | `{"input": ..., "output": ...}`，来自模型定义的 `input_price_per_1k`/`output_price_per_1k` |
| `_usage` | `TokenUsage`（`lubanagent.core.run.TokenUsage`） | 累计 `input_tokens`/`output_tokens`/`model_calls` |
| `_cost` | `float` | 累计估算成本 |

### `CapabilityContext`（`capabilities.py:69-79`，frozen dataclass）

| 字段（代码类型） | 默认值 | 说明 |
|---|---|---|
| `system_suffix: str` | `""` | 追加到 system prompt 尾部的内容，顺序固定为：长期记忆 → 知识片段 → Skill 元数据 |
| `extra_tools: list[RegisteredTool]` | `[]` | `skill_load` 等能力附带工具 |
| `history: list[BaseMessage] \| None` | `None` | 会话/摘要记忆提供的历史；`None` 表示不启用记忆（与 `RunRequest.history` 二选一，见行为章节） |
| `knowledge_no_hit: bool` | `False` | 知识检索经守门过滤后全 miss 且配置了 `no_hit_fallback` 时置 `True`；`agent_loop` 据此短路拒答（不调模型，`output = no_hit_fallback`） |

索引：无（本模块不持久化，无表无索引）。

## 行为

### 状态机

Run 状态（`RunStatus`，定义于 `lubanagent/core/run.py:13-19`，本模块是其唯一状态转移驱动方）：

```
PENDING（未落库，_load_definition/_check_runnable/_resolve_brain 阶段）
  │
  ├─(校验失败: AGENT_NOT_FOUND / AGENT_VERSION_UNRUNNABLE)→ raise LubanError（不产生 Run 记录）
  ├─(CapabilityMismatchError)→ RUN_FAILED 事件（不落 Run 记录，agent_loop.py:305-315）
  │
  ▼ Run 建档（agent_loop.py:577-600）
RUNNING ──(工具审批要求)──▶ WAITING_APPROVAL ──(审批完成)──▶ RUNNING
  │
  ├─(进程重启后 stream_resume，非终态且有无 checkpoint)──▶ RUNNING
  │   （复用已有 run 行，不重建、不重发 RUN_STARTED；发 RUN_RESUMED 事件，agent_loop.py:561-575）
  │
  ├─ 正常完成（无 tool_calls 且有内容）───────────────▶ COMPLETED（end_reason=FINAL_ANSWER）
  ├─ 知识全 miss 短路（knowledge_no_hit）─────────────▶ COMPLETED（end_reason=KNOWLEDGE_NO_HIT，output=no_hit_fallback，未调模型）
  ├─ GraphRecursionError（超 max_steps*2+1）──────────▶ FAILED（end_reason=STEP_LIMIT）
  ├─ BudgetExceededError（token/cost 超限）───────────▶ FAILED（end_reason=BUDGET_EXCEEDED）+ 额外发 BUDGET_EXCEEDED 事件
  ├─ RunTimeoutError（check_time 触发）───────────────▶ FAILED（end_reason=TIMEOUT）
  ├─ RunCancelledError（协作式取消检查点触发）─────────▶ CANCELLED（end_reason=CANCELLED）
  ├─ asyncio.CancelledError（consumer 侧放弃流，如客户端断连）▶ CANCELLED（end_reason=CANCELLED，abandoned=True，最终重新 raise）
  ├─ LubanError（其余框架异常）───────────────────────▶ FAILED（end_reason=ERROR）
  └─ 其余 Exception（兜底）───────────────────────────▶ FAILED（end_reason=ERROR，收敛为 INTERNAL_ERROR + 稳定消息 `内部错误: {类型名}`，异常正文不进 run.error/RUN_FAILED 事件/SSE，`agent_loop.py:627-630`）
```

（状态转移代码位置：`agent_loop.py` 异常→outcome 映射与 `_finalize` 写终态；resume 的状态置位由 `TraceWriter.resume_run` 统一完成。）

### stream_resume 续跑路径（校验在锁内，续跑复用 `_execute`）

```
AgentRuntime.stream_resume(run_id)
├── [本 Runtime 无 checkpointer] raise CONFIG_ERROR（不进锁，agent_loop.py:367-372）
├── async with _resume_lock(run_id)  # per-run 常驻锁，同 run 并发 resume 串行
├── RunRepository.get_run(run_id)
│   ├── [不存在] raise RUN_NOT_FOUND
│   ├── [终态 COMPLETED/FAILED/CANCELLED] raise VALIDATION_ERROR
│   ├── [cancellations.is_active(run_id)] raise VALIDATION_ERROR  # 本进程活跃
│   ├── [parent_run_id 非空] raise VALIDATION_ERROR  # 子 Run 由父编排推进
│   └── [checkpointer.aget_tuple(thread_id="run-{id}") 为空] raise RUN_NOT_FOUND(no_checkpoint)
├── RunRequest(agent_key, user_input/session_id/user_id/variables 取自 run 行, version=run.agent_version)
├── _load_definition → _check_runnable → _resolve_brain  # 定义按 run.agent_version 钉死加载；
│   │                                                    # 大脑按当前 mapping 重新解析
│   └── [解析 model_key ≠ run.model_key] 更新 run 行（如实反映，与图 resume「现状兜底」同语义）
├── asyncio.create_task(_execute(..., resume_run=run))
└── _execute（resume 模式差异，其余与 stream 共用同一代码路径）
    ├── 不建档：复用已有 run 行，TraceWriter.resume_run(run)  # status=RUNNING + RUN_RESUMED 落库，
    │   │                                                      # 同一 trace 行追加（不重发 RUN_STARTED）
    │   └── emit RUN_RESUMED（persist=False，已落库只外发）
    ├── BudgetGuard 重建：RunRepository.run_usage(run_id) 读 token_usages/cost_records 账本求和，
    │   以已消费量初始化（initial_*）——崩溃续跑不重复放水（agent_loop.py:618-629）
    ├── 取消 token 重新注册（可被 cancel）；审批桥照常接线（工具重放会重新签发审批请求）
    └── agent.astream_events(None, config)  # LangGraph null-resume：从 checkpoint 恢复执行
```

### 调用树

#### `AgentRuntime.stream(request)` 主执行路径

一次 Run 从 HTTP/图节点/委派入口调用 `stream()` 开始：先做三步启动前校验（装载定义→可运行性→大脑解析），校验通过后 `asyncio.create_task` 派生 `_execute` 协程，用 `asyncio.Queue` 把内部事件转发给外部消费者（`agent_loop.py:183-217`）；`_execute` 内部依次经过 Run 建档、治理起表、上下文组装、工具桥接、LangGraph 循环、异常收敛与收尾 6 个阶段（编号沿用代码注释 1-11）。

```
AgentRuntime.stream(request)
├── 1. _load_definition(request)  # 装载 AgentDefinition；不存在 → LubanError(AGENT_NOT_FOUND/AGENT_VERSION_NOT_FOUND)，不产生 Run
├── 2. _check_runnable(definition)  # 校验 definition.tools 全部已注册 + signature_hash 未漂移
├── 3. _resolve_brain(definition)  # BrainResolver 解析 fast/smart/cheap/vision → 具体模型；CapabilityMismatchError 转 RUN_FAILED 事件后 return（不落 Run）
├── asyncio.create_task(_execute(...))  # 派生执行协程，经 queue 转发事件
└── _execute(request, definition, resolution, run_id, queue)
    ├── 3b. 降级留痕：resolution.fallback_events 逐个 emit（回填 run_id）
    ├── 4. Run 建档
    │   ├── TraceWriter.start_run(run)  # 落库 status=RUNNING + RUN_STARTED
    │   ├── SafeBackend.start_trace(run)  # 观测后端开 Trace（Langfuse/LangSmith/none）
    │   └── _call_hooks("pre_run", ctx)  # Registry 注册的 hooks，异常只记日志不阻断
    ├── 5. 治理起表
│   ├── guard = request.budget_guard or 新建 BudgetGuard(definition.boundary.budget, timeout_seconds, pricing=call_pricing)  # 预算继承入口；call_pricing=本 Run 模型实际单价（H11）
│   ├── token = CancellationRegistry.create(run_id)
│   ├── ctx.metadata["budget_guard"] / ["cancellation_token"] = guard / token  # 供 graph/multiagent 子 Run 读取
│   ├── token.throw_if_cancelled()  # 起跑前先检查一次
│   └── [call_pricing is None 且本 Run/共享池带 max_cost] emit BUDGET_WARNING（reason=no_pricing + model_key）
    ├── 6. 上下文 + 模型
    │   ├── capabilities.prepare(...)  # 记忆/知识/Skill 按需装配（capabilities.py，见下方子调用树）
    │   ├── [cap_ctx.knowledge_no_hit] 短路拒答分支（agent_loop.py:410-426）
    │   │   ├── last_output = definition.knowledge.no_hit_fallback or ""
    │   │   ├── emit KNOWLEDGE_NO_HIT（payload: sources/score_threshold/hits_raw/hits_kept）
    │   │   ├── outcome = (COMPLETED, EndReason.KNOWLEDGE_NO_HIT, None)  # 不调模型：零 token、无 MODEL_* 事件
    │   │   └── 跳过 7-8 直接进入收尾（capabilities.finalize 照常回写记忆）
    │   ├── [instructions_override] definition = definition.model_copy(...)  # 试跑临时替换，不改版本定义
    │   ├── ContextBuilder.build_system_prompt(definition, variables)  # 稳定前缀
    │   ├── [cap_ctx.system_suffix] 追加到 system_prompt 尾部  # KV-cache: 动态内容后置
    │   ├── 组装 messages：SystemMessage? + (cap_ctx.history ?? ContextBuilder.build_messages(...)) + HumanMessage
    │   └── create_chat_model(resolution.definition)  # lubanagent.model 工厂
    ├── 7. 工具桥接
    │   ├── _executor_for_run(run_id, run, emit)  # 有 ApprovalManager 时包一层审批 gate（WAITING_APPROVAL 状态切换）
    │   └── definition.tools + cap_ctx.extra_tools 逐个 _bridge_tool(...) → StructuredTool
    │       └── bridge(**kwargs)  # 实际工具调用体
    │           ├── emit TOOL_CALL_STARTED
    │           ├── _call_hooks("pre_tool", ctx, descriptor, kwargs)
    │           ├── executor.execute(registered, kwargs, ctx)  # ToolExecutor（tool 模块）
    │           ├── emit TOOL_CALL_COMPLETED / TOOL_CALL_FAILED
    │           └── _call_hooks("post_step", ctx, step)
    ├── 8. 循环执行（LangGraph prebuilt agent）
    │   ├── create_react_agent(model, tools)  # langgraph.prebuilt，模型↔工具循环本体
    │   └── agent.astream_events({"messages": messages}, recursion_limit=max_steps*2+1, version="v2")
    │       ├── on_tool_start → 记录 tool_run_ids（用于过滤嵌套子 Run 事件）
    │       ├── [事件属于工具内嵌套 Run] continue，不做取消/超时检查（见边界规则）
    │       ├── [kind in _CHECKPOINT_EVENTS] token.throw_if_cancelled() + guard.check_time()  # 取消/超时检查点
    │       ├── on_chat_model_start → 记录 model_step_started
    │       ├── on_chat_model_stream → emit MODEL_TOKEN_DELTA
│       └── on_chat_model_end
│           ├── TraceWriter.record_model_usage(...)  # 先落用量明细，撞预算也留痕
│           ├── guard.consume(..., pricing=call_pricing)  # 共享池按当次单价累计，返回本次成本
│           ├── own_usage/own_cost 本地归因累计 + ctx.usage=own_usage  # H11：Run 只落自己的消费
│           ├── guard.enforce()  # 撞线抛 BudgetExceededError（消费已全部入账）
│           ├── _call_hooks("post_step", ctx, step)
│           └── emit MODEL_CALL_COMPLETED
    ├── [异常收敛]  # GraphRecursionError / BudgetExceededError / RunTimeoutError / RunCancelledError / asyncio.CancelledError / LubanError / Exception → outcome
    ├── event_stream.aclose()  # 异常出圈时取消在途工具调用，避免子 Run 变孤儿任务
    ├── 9-11. 收尾
    │   ├── [extra_event] emit(extra_event)  # 目前仅 BUDGET_EXCEEDED
    │   ├── _finalize(...)  # TraceWriter.finish_run + record_cost + SafeBackend.end_trace + drain；返回终态 TraceEvent
    │   ├── capabilities.finalize(...)  # 会话/摘要记忆回写，内部消化异常
    │   ├── CancellationRegistry.discard(run_id)
    │   └── queue.put(terminal or 兜底 RUN_FAILED) + queue.put(_END)
    └── [abandoned] raise asyncio.CancelledError  # consumer 放弃流时保持任务取消语义
```

#### `capabilities.prepare(...)` 子调用树（`capabilities.py:82-193`）

```
prepare(definition, user_input, session_id, user_key, db, registry, skill_registry, summarizer)
├── [全部能力关闭] return CapabilityContext()  # 零副作用 early-return
├── 1. 会话/摘要记忆 → history
│   ├── [memory.summary and summarizer] SummaryMemory.context_prefix(session_id, window_size)
│   └── [仅 memory.session] SessionMemory.history(session_id, window_size)
├── 2. 长期记忆 → suffix_parts
│   └── LongTermMemory.recall(definition.key, user_key) → format_facts_for_prompt
├── 3. 知识检索 → suffix_parts（含防错答守门）
│   ├── [source 未注册] raise LubanError(CONFIG_ERROR)
│   ├── connector.retrieve(user_input, top_k)
│   ├── _filter_snippets(snippets, definition.knowledge)  # score_threshold 阈值 + max_age_days 超龄过滤
│   ├── [过滤后非空] cite=True 强引用约束头 + format_snippets_for_prompt 编号 / cite=False 纯片段
│   └── [过滤后为空 且 no_hit_fallback 非 None] knowledge_no_hit=True（不注入 suffix）
├── 4. Skill 元数据 + skill_load 工具 → suffix_parts / extra_tools
│   └── [与用户工具同名冲突] 改名 luban:<key> + 记 warning
└── return CapabilityContext(system_suffix="\n\n".join(suffix_parts), extra_tools, history, knowledge_no_hit)
```

## 边界规则

- **步数上限**：`create_react_agent` 的 `recursion_limit = definition.boundary.max_steps * 2 + 1`（模型步+工具步各算一次 LangGraph 内部步）；超限由 LangGraph 抛 `GraphRecursionError`，本模块捕获后映射为 `RunStatus.FAILED` + `EndReason.STEP_LIMIT`（`lubanagent/runtime/agent_loop.py:489-495`、`592-593`）。`max_steps` 定义在 `lubanagent/core/definition.py:51`（`BoundaryConfig.max_steps`，默认 16）。
- **预算超限熔断**：记账顺序代码强制为「用量账本 → 共享池累计（`consume`）→ 本地归因 → 限额判定（`enforce`）」，四步之间无 await（并行子 Run 消费原子累计，不跨 await 用过期余额）；`enforce()` 对 token/cost 任一超限抛 `BudgetExceededError`，此时本次消费已全部入账（失败/取消/重试路径消费不消失）；`agent_loop._execute` 捕获后映射为 `FAILED` + `EndReason.BUDGET_EXCEEDED`，并额外发一条 `TraceEventType.BUDGET_EXCEEDED` 事件。若配置了 `max_cost`（含共享池继承）但本 Run 模型无单价，起跑即发 `BUDGET_WARNING`（payload 含 model_key，不阻断执行，不静默承诺费用硬上限）。
- **超时**：`BudgetGuard._deadline = time.monotonic() + min(timeout_seconds, budget.max_seconds or timeout_seconds)`（`budget_guard.py:19-23`），`check_time()` 在取消/超时检查点被调用，过期 `raise RunTimeoutError`（`budget_guard.py:28-31`），映射为 `FAILED` + `EndReason.TIMEOUT`（`agent_loop.py:602-604`）。`timeout_seconds` 来自 `BoundaryConfig.timeout_seconds`（默认 300 秒，`core/definition.py:52`）。
- **取消传播**：协作式取消——`CancellationToken.cancel()` 只置位 `threading.Event`，实际中断发生在下一个检查点（`cancellation.py:8-23`）。检查点白名单 **代码强制** 为 `_CHECKPOINT_EVENTS = {on_chat_model_start, on_chat_model_stream, on_chat_model_end, on_tool_end, on_tool_error}`（`agent_loop.py:75-83`）；框架/路由类 chain 事件和工具的 framing 事件不做检查点，原因是工具协程此时可能尚未落图 Run，`throw` 会导致事件流 `aclose()` 强杀在途工具协程、丢失确定性终态（`agent_loop.py:72-83`、`509-515` 注释）。工具内嵌套 Run（`flow_as_tool` → 图/子 Agent）产生的 LangChain 事件通过 `tool_run_ids` 集合 + `parent_ids` 交集过滤掉，**不**在父 Run 侧对这些事件做取消/超时检查，避免父 Run 提前 throw 连带强杀子 Run 协程；取消传播给子 Run 由子图节点边界代码（`graph/runner.py`）通过共享的 `ctx.metadata["cancellation_token"]` 强制执行（`agent_loop.py:494-508`）。
- **预算继承（子 Run 共享父预算池）**：`RunRequest.budget_guard` 非空时直接复用同一个 `BudgetGuard` 实例（成员消费累计进同一池），为空才按自身 `definition.boundary.budget` 新建。该共享 guard 经 `ctx.metadata["budget_guard"]` 向下游图节点/委派子 Run 传递，下游读取逻辑见 `graph/runner.py`（`_resolve_guard`：父 guard 共享 → 父 budget 自建 → 图自身 budget → 不 guard）与 `multiagent/team.py`（同构逻辑）。此契约对应技术方案 §10.6（预算继承、取消传播，子 Run 共库同治理）。
- **本地成本归因（H11）**：每次模型调用按**当次 Run 模型的实际单价**计费（父子/成员模型价格不同不串价；显式无单价成本记 0，不回落其他模型单价）；Run 行（`runs.token_usage`/`runs.cost`）与费用账本（`cost_records`）只落本 Run 自己的消费，不落共享池累计值；父自身与后代聚合走报表口径（`CostRepository.run_tree_cost` / `build_run_tree_cost_report`：own 与 descendants 明示分开，total = own + descendants，不重复相加）。`ctx.usage` 语义为本 Run 归因值；共享池值经 `guard.usage`/`guard.cost` 获取。
- **KV-cache 友好：稳定前缀 + append-only 不变量**：`ContextBuilder.build_system_prompt` 只拼 `role` + `instructions` 两个静态段（不含动态内容），保证 system prompt 前缀在同一 Agent 版本内跨请求稳定（`context_builder.py:18-25`）；能力件产出的动态内容（长期记忆/知识片段/Skill 元数据）**只追加到 system prompt 尾部**、不插入前缀中间（`agent_loop.py:436-442`，注释明确标注"KV-cache 语义：能力内容动态后置到 system prompt 尾部，稳定前缀不变"）；消息历史侧 `build_messages` 对 `history` 只做尾部截断（保留最新）+ 追加一条新 `HumanMessage`，不做历史内容的原地改写（`context_builder.py:27-37`，append-only）。该约束在模块 docstring 中明确为"尽力而为非硬不变量"（`context_builder.py:1`）——代码提供组装规则、不做运行期断言（热路径重算比对是同义反复，跨轮断言需 runtime 持有 per-session 状态，见测试 docstring 的决策说明）；但已有**测试级硬约束守卫**（风险 R1）：`tests/lubanagent/runtime/test_kv_cache_prefix_invariant.py` 用测试内独立构造的期望前缀（刻意不经 `ContextBuilder` 自算，防组装器自证清白）断言同一 session 连续两轮 SystemMessage 均以稳定前缀逐字节开头、动态内容只增其后、历史 append-only 不改写——把动态内容前插/穿插进前缀的改动会让测试红。
- **启动前失败不产生 Run 记录**：`_load_definition`/`_check_runnable` 阶段的 `LubanError` 直接向上抛出，不经过 Run 建档流程，因此不会在 DB 留下记录（`agent_loop.py:186-189`、`AgentRuntime.run` 的 `RUN_NOT_FOUND` 分支印证此契约，`agent_loop.py:219-232`）。`CapabilityMismatchError`（大脑解析失败）同样在建档前被捕获，只发 `RUN_FAILED` 事件不落 Run（`agent_loop.py:191-199`）。
- **终态唯一性**：无论正常完成、异常收敛还是 `finally` 兜底，`_execute` 保证队列中最终恰好出现一个终态事件（`RUN_COMPLETED`/`RUN_FAILED`/`RUN_CANCELLED`）后跟一个 `_END` 哨兵；`_finalize` 抛异常时有兜底 `RUN_FAILED` 事件补发（`agent_loop.py:661-676`）。
- **SSE 外发白名单**：`sse_stream` 只转发 `SSE_EVENT_MAP` 覆盖的事件类型，`STEP_STARTED`/`STEP_COMPLETED`/`MODEL_CALL_STARTED` 等内部粒度 Trace 事件默认不外发给客户端（`sse.py:10-26`，代码强制而非约定）；外发帧 payload 经 `redact_value` 脱敏后再拼帧（`sse.py:39`），即使上游已脱敏也再兜一层（纵深防御，H07）。
- **Run 错误收敛：异常正文不进 run.error/RUN_FAILED 事件/SSE（H07）**：异常→error dict 统一走 `_error_dict`（`agent_loop.py:112-120`）——`LubanError` 用其 `to_dict()["error"]` 但 `details` 经 `redact_value` 脱敏（`_redact_error_dict`，`agent_loop.py:124-131`）；非 `LubanError` 异常收敛为 `INTERNAL_ERROR` + 稳定消息 `内部错误: {类型名}`（`agent_loop.py:121-122`），不再拼 `str(exc)`——SDK/上游正文/URL/本机路径不进 `Run.error`、`RUN_FAILED` 事件与 SSE；完整堆栈仅进日志（`agent_loop.py:628` `logger.exception`）。异常收敛各分支（GraphRecursionError/BudgetExceededError/RunTimeoutError/LubanError/兜底 Exception）全部经 `_error_dict`（`agent_loop.py:604-630`）。
- **Trace 事件 payload 的审计副本脱敏，执行仍用真实参数（H07）**：工具/模型事件的审计副本经 `redact_value` 脱敏——`TOOL_CALL_STARTED` 的 `args`（`agent_loop.py:811`）、`TOOL_CALL_COMPLETED` 的 `result`（`agent_loop.py:825`）、`TOOL_APPROVAL_REQUESTED` 的 `args`（`agent_loop.py:760`）、`MODEL_CALL_COMPLETED` 的 `tool_calls[].args`（`agent_loop.py:592`）、`TOOL_CALL_FAILED` 的 `error.details`（`agent_loop.py:837`）；而工具 handler 执行与审批处理仍接收真实参数（执行与审计副本分离，`agent_loop.py:814-818`）。测试：`tests/lubanagent/runtime/test_agent_loop.py`（通用异常稳定消息 + 工具事件脱敏但 handler 收真实值）。
- **知识全 miss 短路拒答（防错答守门的执行点）**：`capabilities.prepare` 置 `knowledge_no_hit=True`（守门过滤后 snippets 为空且 `no_hit_fallback` 非 None）时，`_execute` 在上下文组装后、模型执行前短路：`last_output = no_hit_fallback`，emit `KNOWLEDGE_NO_HIT` 事件（payload：`sources`/`score_threshold`/`hits_raw`/`hits_kept`——过滤前后条数，阈值校准排障观测点），`outcome = (COMPLETED, EndReason.KNOWLEDGE_NO_HIT, None)`（`agent_loop.py:410-426`）。短路路径**不调模型**——零 token 消耗、无 MODEL_* 事件；Run 仍走正常终态链路（`_finalize` 落终态、`capabilities.finalize` 把兜底话术作为 assistant 消息回写会话记忆）。守门字段与过滤语义详见 `docs/specs/modules/knowledge-memory.md`。
- **resume 恢复从最新 checkpoint 重放，工具副作用可能重复执行**：`LubanCheckpointer` 无 writes 表（`aput_writes`/`put_writes` no-op，`persistence/checkpoint.py:76-84、116-123`），恢复（`stream_resume` → null-resume）从最新 checkpoint 重放最后一个未完成步骤——工具可能被再次真实执行；无业务幂等保障时**不承诺 exactly-once**（与图 Run resume 同边界）。重放可观测：`tests/lubanagent/runtime/test_resume.py::test_crash_prefix_recovery` 断言 `TOOL_CALL_COMPLETED` 计数为 2（首轮 + 重放）。
- **resume 后步数与超时重新起算**：LangGraph 的 recursion 计数器不持久化，resume 后 `recursion_limit = max_steps * 2 + 1` 重新计数——步数硬上限可被崩溃续跑绕过一轮（如实声明，非缺陷修复项）；`BudgetGuard` 按 definition boundary 重建（以 `run_usage` 账本恢复已消费 token/cost，**预算不放水**），超时 deadline 从 resume 时刻重新起算（`budget_guard.py:19-23` 构造期设定）。
- **resume 的记忆回写仅终态一次**：会话/摘要记忆回写只发生在 `_finalize`（`capabilities.finalize`）——崩溃中断的非终态 Run 从未走到 finalize，多次中断-续跑后只有推进到终态的那一次执行回写，不重复回写；已终态 Run 被 resume 显式拒绝，不存在二次回写路径。

## 名词

| 术语 | 语义 |
|---|---|
| Run | 一次 Agent 执行的完整生命周期记录（`lubanagent.core.run.Run`），本模块是其唯一状态转移驱动方；子 Run 通过 `parent_run_id` 关联父 Run |
| BudgetGuard | 运行预算三道闸（token/cost/时长）的内存对象，撞线即抛异常（代码强制熔断），可跨 Run 共享以实现预算继承（`budget_guard.py:8`） |
| CancellationToken | 单次 Run 的协作式取消令牌，`threading.Event` 承载，需在检查点主动调用 `throw_if_cancelled()` 才生效（非抢占式）（`cancellation.py:8`） |
| 检查点（Checkpoint） | `_CHECKPOINT_EVENTS` 白名单覆盖的 LangGraph 事件类型（模型三事件 + 工具收尾两事件），是取消/超时唯一生效的时机点（`agent_loop.py:75-83`） |
| RUN_RESUMED | 续跑登记事件：`TraceEventType.RUN_RESUMED`（SSE 外发名 `run.resumed`），payload `{"resumed": true}`；由 `TraceWriter.resume_run` 落库到原 Run 的同一 trace 行（不重建 trace、不重发 `RUN_STARTED`），`stream_resume` 随后只外发不重复落库（`core/trace.py:14`、`observability/trace/writer.py:48-62`） |
| run 线程（`run-{run_id}`） | 普通 Run 启用 checkpoint 后的 LangGraph `thread_id` 命名约定（`configurable.thread_id = f"run-{run_id}"`），与图编排线程（`uuid4().hex`）命名空间隔离；同一 `checkpoints` 表、两种前缀语义（`agent_loop.py:768-772`） |
| KV-cache 友好 | system prompt 组装遵循"稳定前缀（role+instructions）+ 动态内容尾部追加 + 历史 append-only"的规则，目的是让模型侧/网关侧的 KV-cache 复用率最大化；运行期不校验（热路径不做断言），由守卫测试 `test_kv_cache_prefix_invariant.py` 测试级强制（`context_builder.py:1`、`tests/lubanagent/runtime/test_kv_cache_prefix_invariant.py:1-25`） |
| CapabilityContext | `capabilities.prepare()` 的产出，携带 system prompt 尾部追加内容、额外工具、历史消息、知识全 miss 标志（`knowledge_no_hit`）四项，供 `agent_loop` 消费（`capabilities.py:69-79`） |
| SSE 事件类型 | `SSE_EVENT_MAP` 定义的对外事件名（如 `run.started`/`token.delta`/`tool.finished`/`knowledge.no_hit`），是内部 `TraceEventType` 的子集+改名映射，用于 HTTP SSE 协议外发（`sse.py:9-24`） |
| KNOWLEDGE_NO_HIT | 知识检索经守门过滤后全 miss 且配置 `no_hit_fallback` 时的事件与结束原因：`TraceEventType.KNOWLEDGE_NO_HIT`（SSE 外发名 `knowledge.no_hit`）+ `EndReason.KNOWLEDGE_NO_HIT`（Run 仍 COMPLETED，未调模型）（`agent_loop.py:410-426`、`sse.py:23`） |
| 预算继承 | 子 Run 通过 `RunRequest.budget_guard` 复用父 Run 的同一个 `BudgetGuard` 实例，成员消费累计进同一预算池（技术方案 §10.6），落地于 `agent_loop.py:363-378`、`graph/runner.py:129-139`、`multiagent/team.py:281-293` |
| 取消传播 | 父 Run 的 `CancellationToken` 经 `ctx.metadata["cancellation_token"]` 传给图节点/委派子 Run，子 Run 在自身检查点独立生效，父子互不强杀在途协程（`agent_loop.py:382`、`477-491`） |

## 上下游

**上游依赖**（本模块消费外部数据/能力）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.core` | `AgentDefinition`/`Run`/`RunContext`/`TraceEvent`/`Budget`/各错误类型（`core/__init__.py`） | 纯数据模型与错误体系；本模块不定义领域模型，只消费 |
| `lubanagent.persistence` | `AgentRepository.get_agent/get_version`、`ModelRepository`、`RunRepository.get_run/update_run/run_usage`、`LubanCheckpointer`（checkpointer 注入，本体在 persistence） | Agent 定义装载、Run 记录读回、resume 预算账本求和（`run_usage`：token_usages + cost_records 合计）、checkpoint 落库（经注入的 Saver，非本模块直写） |
| `lubanagent.model` | `BrainResolver`/`resolve_brain_for_agent`/`create_chat_model` | 逻辑大脑槽位解析为具体模型、构造 LangChain `ChatModel` 实例（`agent_loop.py:43-48`、`287-290`、`437`） |
| `lubanagent.tool` | `ApprovalManager`/`ToolExecutor`/`model_from_schema` | 工具执行、审批 gate、参数 schema 转 pydantic 模型（`agent_loop.py:56-57`、`711-826`） |
| `lubanagent.observability` | `SafeBackend`/`TraceWriter`/`resolve_backend` | Trace 落库（source of truth）与可插拔观测后端外发（`agent_loop.py:49`、`168-179`） |
| `lubanagent.extension` | `ObservabilityBackend`/`RegisteredTool`/`Registry`/`default_registry` | 工具/知识源/hooks 的运行期注册表，本模块通过它找用户扩展（`agent_loop.py:37-42`） |
| `lubanagent.memory` / `lubanagent.knowledge` / `lubanagent.skill` | `SessionMemory`/`SummaryMemory`/`LongTermMemory`/知识 connector/`SkillRegistry` | `capabilities.prepare/finalize` 装配的可选能力件（`capabilities.py:14-25`） |
| `langgraph.prebuilt` | `create_react_agent` | 模型↔工具循环本体（LangGraph 承担，本模块只治理包装）（`agent_loop.py:489-491`） |
| `lubanagent.config` | `Settings` | 环境配置（`env` 用于 `BrainResolver` 环境相关映射）（`agent_loop.py:18`、`289`） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent.api`（`api/routes/runs.py`） | `POST /agents/{key}/runs`（`AgentRuntime.stream` + `sse_stream`）、`POST /runs/{run_id}/resume`（`AgentRuntime.stream_resume`，SSE 帧格式同创建）、`POST /runs/{run_id}/cancel`（`AgentRuntime.cancel`） | HTTP 层薄封装本模块的 Python API 对外提供 SSE 流式创建/续跑/取消 Run（错误均在流开始前走统一错误映射） |
| `lubanagent.graph`（`graph/runner.py`） | `GraphRunner` 节点内 `AgentRuntime.run(RunRequest(parent_run_id=..., budget_guard=...))` | 手写 LangGraph 图中"节点=Agent"场景，把节点执行委派给本模块跑一次子 Run，并共享预算/取消（`graph/runner.py:38-43`、`129-139`、`350-361`） |
| `lubanagent.multiagent`（`multiagent/team.py`、`multiagent/supervisor.py`） | `_TeamInvocation`/`SupervisorRouter.decide` 内 `AgentRuntime.run(RunRequest(parent_run_id=..., budget_guard=...))` | 多 Agent 编排（supervisor/团队委派）把成员 Agent 与路由决策都作为子 Run 跑在本模块之上，预算继承+取消传播语义与 `graph` 一致（技术方案 §10.6） | 
| `lubanagent.ui`（`ui/src/lib/sse.ts`、`ui/src/components/run-events.ts`） | 前端消费 `sse_stream` 产出的 SSE 帧 | 工作台页面渲染 Run 实时事件流（token 增量、工具调用、终态） |
| `lubanagent.cli`（`cli/main.py`） | CLI 命令内构造 `RunRequest` 调用 `AgentRuntime` | 命令行试跑 Agent |
| `lubanagent.observability.evals`（`evals/runner.py`、`evals/replay.py`） | 评测跑分/录制重放消费 `AgentRuntime` 产出的 `TraceEvent`/`Run` | 评测体系基于本模块的执行结果做断言与回归对比 |
