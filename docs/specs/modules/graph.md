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

# graph 模块

## 定位

`lubanagent/graph` 是"一个引擎两种用法"中**手写图**形态的实现：普通 Agent 用 prebuilt agent + 治理包装跑对话循环（对应 `runtime` 模块），确定性流程则用本模块的 DSL 手写 LangGraph 图（来源：`lubanagent/graph/__init__.py:1` 模块 docstring "graph：确定性流程编排（一个引擎两种用法之手写图形态），子 Run 语义与单 Agent 同治理"）。核心不是数据库实体，而是内存态的 DSL 构建产物：`GraphBuilder`（可变构建器）→ `CompiledFlow`（拓扑校验通过的不可变定义，`lubanagent/graph/builder.py:351-364`）→ 每次 `GraphRunner.run()` 时再编译为 LangGraph `CompiledStateGraph`（`lubanagent/graph/builder.py:268-295`）。图执行落库为 `runs` 表的一条子 Run（`agent_key=graph:<flow_name>`、`agent_version=0`，`lubanagent/graph/runner.py:194-209`），节点流转的持久化实体是 `checkpoints` 表（`lubanagent/persistence/models/checkpoint.py:13-25`），供人工中断（`human` 节点）后跨进程恢复。

## 接口契约

### HTTP

本模块不定义路由（import-linter 禁止 graph 依赖 `lubanagent.api`），但被 `lubanagent/api/routes/graphs.py` 消费：`GET /v1/graphs`（列表，`structure_available` 标记 DSL 可视性）与 `GET /v1/graphs/{name}`（拓扑详情）实时调 `descriptor.builder()` 经 `.structure()` 读 DSL 结构渲染只读视图（工作台「流程图」页），不触发编译（详见 `docs/specs/modules/api-app-cli.md` graphs.py 段）。

### RPC / MQ / Cron

无。本模块对外暴露的是 Python API（DSL 方法链 + `GraphRunner` 方法），而非网络接口。以下按 Python 公开方法签名列出：

**`GraphBuilder`**（`lubanagent/graph/builder.py:105-237`，DSL 面，链式返回 `self`）：

| 方法 | 签名 | 说明 |
|---|---|---|
| `__init__` | `(name: str, *, description: str = "")` | 构建器命名（`builder.py:109-115`） |
| `.entry(name)` | `(name: str) -> GraphBuilder` | 指定入口节点（`builder.py:119-121`） |
| `.step(name, fn)` | `(name: str, fn: StepFn) -> GraphBuilder` | 节点=普通 Python 函数，`state → dict` 更新或完整 state，支持 async（`builder.py:123-125`） |
| `.agent(name, agent_key, *, input_from="input", output_to="agent_output")` | `(...) -> GraphBuilder` | 节点=Agent：从 `state[input_from]` 取输入跑 `AgentRuntime`，结果写 `state[output_to]`（`builder.py:127-140`） |
| `.human(name, *, prompt="")` | `(name: str, *, prompt: str = "") -> GraphBuilder` | 人工节点：LangGraph `interrupt` 暂停，resume 值写回 `state[name]`（`builder.py:142-145`） |
| `.when(src, cond, *, then, otherwise)` | `(src: str, cond: Callable[[dict], bool], *, then: str, otherwise: str) -> GraphBuilder` | 条件边（`builder.py:147-156`） |
| `.edge(a, b)` | `(a: str, b: str) -> GraphBuilder` | 普通边（`builder.py:158-160`） |
| `.build()` | `() -> CompiledFlow` | 拓扑校验：entry 存在、边引用节点存在、无孤立节点、普通边与条件边不能共用同一 `src`，否则抛 `LubanError(VALIDATION_ERROR)`（`builder.py:193-232`） |
| `.structure()` | `() -> GraphStructure` | 只读结构快照（不校验不编译）：`GraphStructure{name, description, entry, nodes, edges, cond_edges}`；node 含 `kind/agent_key/input_from/output_to/prompt/summary`（step 的 summary 取 fn docstring 首行作节点含义），`fn`/`cond` 等可执行体本身不暴露——`GET /v1/graphs` 图结构视图的数据来源（`builder.py:69-103, 179-205`） |
| `.has_agent_nodes` | `@property -> bool` | 是否含 `.agent()` 节点（`builder.py:165-167`） |
| `__call__` | `(*, hooks=None, checkpointer=None) -> CompiledStateGraph` | `GraphDescriptor.builder` 语义的可调用入口，实为编译（`builder.py:234-237`） |

**`CompiledFlow`**（`@dataclass(frozen=True)`，`builder.py:351-364`）：字段 `name: str`、`description: str`、`builder: GraphBuilder`；方法 `has_agent_nodes -> bool`、`to_graph_descriptor() -> GraphDescriptor`（供 `@graph` 装饰器/`Registry.register_graph` 注册，`lubanagent/extension/decorators.py:72-86`）。

**`GraphRunner`**（`lubanagent/graph/runner.py:61-385`）：

| 方法 | 签名 | 说明 |
|---|---|---|
| `__init__` | `(*, db: Database, runtime: AgentRuntime \| None = None, registry: Registry \| None = None, checkpointer: LubanCheckpointer \| None = None, budget: Budget \| None = None)` | `runtime=None` 时含 `.agent()` 节点的图会在 `run/resume` 前校验失败（`runner.py:64-78, 141-147`） |
| `.run(flow, input, *, thread_id=None, parent_ctx=None)` | `(CompiledFlow, dict, *, thread_id: str \| None = None, parent_ctx: RunContext \| None = None) -> GraphResult` | 新线程起跑；`thread_id` 缺省 `uuid4().hex`（`runner.py:82-102`） |
| `.resume(flow, thread_id, value, *, parent_ctx=None)` | `(CompiledFlow, str, Any, *, RunContext \| None) -> GraphResult` | `Command(resume=value)` 续跑；父级治理绑定按「显式传入 > 冻结绑定 > 兜底」重建（见边界规则）；线程不存在抛 `LubanError(RUN_NOT_FOUND)`；Run 已终态（COMPLETED/FAILED/CANCELLED）抛 `LubanError(VALIDATION_ERROR)`（H12 幂等边界）；同线程并发 resume 经 per-thread 锁串行（`runner.py:123-190`） |

**`LubanCheckpointer`**（实现在 `lubanagent/persistence/checkpoint.py:35-291`，经 `lubanagent.persistence` 导出；`lubanagent/graph/checkpoint.py` 保留为 re-export 垫片——普通 Agent Run 复用同一 Saver 而分层契约禁止 `runtime → graph` 依赖，persistence 是共同下层，继承 `BaseCheckpointSaver`）：`__init__(db: Database)`；async 主路径 `aget_tuple/aput/alist/adelete_thread/aput_writes`；sync 兼容包装 `get_tuple/put/list/delete_thread/put_writes`（内部经独立短生命周期 `Database` + `asyncio.run` 或子线程跑新事件循环，`persistence/checkpoint.py:260-289`）。同一实例同时服务两类消费者：本模块 `GraphRunner` 的手写图/多 Agent 编排，与 `runtime.AgentRuntime` 注入的普通 Agent Run 逐步 checkpoint。

**`flow_as_tool`**（`lubanagent/graph/tool_adapter.py:14-59`）：`(flow: CompiledFlow, runner: GraphRunner, *, key: str | None = None, description: str | None = None, risk: RiskLevel = RiskLevel.MEDIUM) -> RegisteredTool`；`args_schema` 仅 `{"input": str}`（`additionalProperties: True`）；`handler(input=None, _ctx: RunContext | None = None)` 声明 `_ctx` 供 `ToolExecutor._accepts_ctx` 识别并注入父 Run 的 `RunContext`（`lubanagent/tool/executor.py:89-96, 244-246`）。

## 数据模型

### checkpoints

对应实体：LangGraph `Checkpoint`/`CheckpointTuple`（`langgraph.checkpoint.base`），由 `LubanCheckpointer` 序列化落库（`lubanagent/persistence/models/checkpoint.py:13-25`）。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| thread_id (String(128)) | thread_id (str) | ✓（主键之一） | — | 图执行线程标识，`GraphRunner.run` 缺省 `uuid4().hex` |
| checkpoint_ns (String(128)) | checkpoint_ns (str) | ✓（主键之一） | `""` | LangGraph 命名空间（本模块未显式设置非空值，`persistence/checkpoint.py:132` 用 `conf.get("checkpoint_ns", "")`） |
| checkpoint_id (String(128)) | checkpoint_id (str) | ✓（主键之一） | — | uuid6（时间有序）；查最新时按字典序倒序取第一条（`persistence/checkpoint.py:141-143` 注释） |
| parent_checkpoint_id (String(128)) | parent_checkpoint_id (str \| None) | — | NULL | 指向上一个 checkpoint，用于恢复父子链（`persistence/checkpoint.py:186`） |
| type (String(64)) | type (str) | ✓ | — | `JsonPlusSerializer.dumps_typed` 的 serde 类型标记（`persistence/checkpoint.py:179, 187`） |
| checkpoint (LargeBinary) | checkpoint (bytes) | ✓ | — | serde 序列化后的 LangGraph `Checkpoint` 二进制内容 |
| metadata (JSON，属性名 metadata_) | metadata_ (dict) | — | `dict`（Row 定义默认；写入时来自 `CheckpointMetadata`） | LangGraph 元数据（`checkpoint.py:23` 注释：metadata 是 Declarative 保留名，属性加下划线，列名仍是 metadata） |
| created_at (DateTime(timezone=True)) | created_at (datetime) | — | `func.now()` | 建表时间戳，本模块代码未读取该列 |

索引：
- 主键 `(thread_id, checkpoint_ns, checkpoint_id)` — 联合主键，支撑按线程+命名空间+具体版本定位（`checkpoint.py:16-18`），无额外二级索引（`lubanagent/persistence/models/checkpoint.py` 全文无 `Index` 声明）。

说明：`aput_writes`/`put_writes` 恒为 no-op，不落 writes 表（本模块无该表，代码注释："pending writes 不落库（无 writes 表）：单 interrupt 节点的 resume 由 langgraph 的 null-resume 通道在内存内完成"，`persistence/checkpoint.py:36-42, 76-84, 116-123`）。

图执行的 Run 记录复用 `runs` 表（非本模块新建，属 `persistence` 模块所有），`GraphRunner._start_run` 写入时的特殊约定：`agent_key=f"graph:{flow.name}"`、`agent_version=0`（`_STUB_VERSION`）、`session_id=thread_id`、`parent_run_id` 透传自 `parent_ctx.run_id`（`runner.py:184-209`）；`user_id` 同样透传自 `parent_ctx.user_id`（透传观测标签，子 Run 继承，`runner.py:199`）；因 `runs.agent_id`/`agent_version_id` 是外键（`lubanagent/persistence/models/run.py:18-19`），`GraphRunner._ensure_stub_agent` 会在 `agents`/`agent_versions` 表按需插入一条 stub 记录（`status=PUBLISHED`，`runner.py:149-182`）。

## 行为

### 状态机

DSL 构建的图本质是节点+边组成的有向图（非严格状态机，因条件边 `.when()` 是唯一分支机制、无独立状态枚举），节点三种 `kind`（`builder.py:36-42`）：

- `step`：执行 `fn(state["data"])`（同步或 async），返回值 `merge` 进 `state["data"]`（`builder.py:298-312`）。
- `agent`：取 `state[input_from]` 作为用户输入，经 `hooks.run_agent(agent_key, user_input)` 跑一次 `AgentRuntime`（子 Run），结果写 `state[output_to]`；若 `GraphRunner` 未注入 `runtime`，`hooks.run_agent is None` 时抛 `LubanError(CONFIG_ERROR)`（`builder.py:314-335`）。
- `human`：调用 LangGraph `interrupt({"node": name, "prompt": prompt, "state": dict(state["data"])})` 暂停执行，resume 后的值写入 `state[name]`（`builder.py:337-347`）。

边分两种：普通边 `.edge(a, b)` 与条件边 `.when(src, cond, then=, otherwise=)`；`build()` 强制二者互斥（同一 `src` 不能既有普通边又有条件边，`builder.py:150-154`）。所有节点隐式 `START → entry`（`_compile` 中 `graph.add_edge(START, self._entry)`，`builder.py:278`）；未被任何边指向且非条件边源的"汇点"节点自动接 `END`（`builder.py:290-294`，demo 级 DSL 省去手写 `.edge(x, END)`）。

以退款流水线（`app/graphs/refund_flow.py:76-94`）为具体状态转移案例：

```
check_eligibility --edge--> compute_amount
compute_amount --when(amount>500)--> approve(human)         [then 分支]
compute_amount --when(amount>500)--> execute_refund           [otherwise 分支]
approve --edge--> execute_refund
execute_refund --edge--> notify
notify --(汇点，自动)--> END
```

### 调用树

#### GraphRunner.run（新线程起跑，以退款流水线为例）

`app/graphs/refund_flow.py` 中的图经 `@graph(name="refund_flow", ...)` 注册（`refund_flow.py:97-99`），由框架层某处（未在本模块范围内）取出 `CompiledFlow` 交给 `GraphRunner.run` 执行。以 `runner.run(flow, input, thread_id=None, parent_ctx=None)` 为入口（`runner.py:82-102`），2 句话概括：不含 `.agent()` 节点时无前置限制；含 `.agent()` 节点但 `GraphRunner` 未注入 `runtime` 则直接拒绝执行；副作用是每次调用都会在 `runs`/`checkpoints` 表落库。

```
GraphRunner.run
├── _check_runnable(flow)  # 校验：含 agent 节点但无 runtime → CONFIG_ERROR
├── _start_run(flow, input, thread_id, parent_ctx)
│   ├── _ensure_stub_agent(flow)  # agents/agent_versions 表兜底一条 stub 行（version=0）
│   └── trace_writer.start_run(run)  # runs 表插入一条 RUNNING 记录
├── _hooks(flow, run, parent_ctx, guard)  # 组装 NodeHooks：取消/预算/Trace/run_agent 闭包
├── flow.builder._compile(hooks, checkpointer)  # 编译为 LangGraph CompiledStateGraph
├── compiled.ainvoke({"data": input}, config)  # 真正执行；LangGraph 逐节点跑 _wrap 包装函数
│   └── [每节点] check_cancelled → check_budget → on_node_started(Trace STEP_STARTED)
│       → 执行 step/agent/human 逻辑 → on_node_completed(Trace STEP_COMPLETED)
└── _outcome(flow, run, thread_id, result)
    ├── [遇 human 节点 interrupt] run.status=WAITING_APPROVAL，返回 GraphResult(status="interrupted", interrupt=...)
    └── [无 interrupt] _finalize(run, COMPLETED, FINAL_ANSWER)，返回 GraphResult(status="completed", ...)
```

#### GraphRunner.resume（人工审批后续跑）

谁发起：业务侧/API 层（不在本模块）拿到 `interrupted` 的 `thread_id` 后调用；前置限制：`checkpoints` 表必须已有该 `thread_id` 的记录，否则 `RUN_NOT_FOUND`；副作用：`runs` 表同一条记录状态从 `WAITING_APPROVAL` 流转回 `RUNNING` 再到终态。

```
GraphRunner.resume
├── _check_runnable(flow)
├── async with _resume_lock(thread_id)  # H12：同线程并发 resume 串行（先到者推进，后到者拒绝）
├── checkpointer.aget_tuple(config)  # 判断线程是否存在
│   └── [不存在] raise LubanError(RUN_NOT_FOUND)
├── _load_run(flow, thread_id)  # 按 agent_key=graph:<flow>, session_id=thread_id 查最近一条 Run
│   └── [status ∈ {COMPLETED, FAILED, CANCELLED}] raise LubanError(VALIDATION_ERROR)  # H12：终态拒绝重复 resume
├── run.status = RUNNING → RunRepository.update_run(run)
├── ctx = parent_ctx if parent_ctx is not None else _restore_parent_ctx(thread_id, run)  # 父级治理绑定重建（runner.py:150）
│   └── _restore_parent_ctx  # runner.py:184-205：从 interrupt 时冻结的 _ParentBinding 重建 RunContext
│       ├── [无绑定（跨进程重启/新 runner 实例）] 返回 None → 走现状兜底（runner 自身 budget）
│       └── [有绑定] metadata 回填同一 guard/token 对象；run_id 取 run.parent_run_id（原始父子关系，trace 链路不断）
├── guard = _resolve_guard(ctx)  # 预算继承链三级 fallback（见边界规则）
├── _parent_bindings[thread_id] = _freeze_binding(ctx, guard)  # 绑定表刷新为本次生效的 ctx（多次 interrupt 同样能回接，runner.py:153）
├── _hooks(flow, run, parent_ctx=ctx, guard=guard)
├── flow.builder._compile(hooks, checkpointer)
├── compiled.ainvoke(Command(resume=value), config)  # LangGraph 从 checkpoint 恢复并注入 resume 值
└── _outcome(...)  # 同 run 路径：可能再次 interrupted（多个 human 节点）或 completed；终态时清除冻结绑定（runner.py:327、337）
```

## 边界规则

- **拓扑校验只在 `build()` 一次性完成，运行期不再校验**：`GraphBuilder.build()` 强制 entry 存在、边引用节点存在、无孤立节点、普通边/条件边互斥，违反抛 `LubanError(ErrorCode.VALIDATION_ERROR)`（来源：`lubanagent/graph/builder.py:131-170`）。
- **含 `.agent()` 节点的图必须注入 `runtime` 才可执行**：`GraphRunner._check_runnable` 在 `run`/`resume` 前检查，`flow.has_agent_nodes and self._runtime is None` 时抛 `LubanError(ErrorCode.CONFIG_ERROR)`（来源：`lubanagent/graph/runner.py:141-147`）；节点执行期再次兜底检查 `hooks.run_agent is None`（来源：`lubanagent/graph/builder.py:259-264`）。
- **人工节点（human）中断时 checkpoint 必须已落库**：`interrupt()` 触发后 LangGraph 框架自动经 `checkpointer.aput` 落一条 `checkpoints` 行，`GraphRunner._outcome` 检测到 `result["__interrupt__"]` 非空即把 `run.status` 置为 `WAITING_APPROVAL`（来源：`lubanagent/graph/runner.py:224-241`；契约文档 `docs/contracts/D3-4-graph.md:93` 明确"暂停时 checkpoint 已落库"为代码强制项，测试佐证 `tests/lubanagent/graph/test_refund_pipeline.py:66-73`）。
- **人工节点 resume 值的落点是 `state[name]`（节点名本身）**：`human_node` 把 `interrupt()` 返回值写为 `data = {**state["data"], name: value}`（来源：`lubanagent/graph/builder.py:280-283`），测试验证 `resumed.output["approve"] == {"approved": True}`（`tests/lubanagent/graph/test_refund_pipeline.py:84`）。
- **预算/取消仅在节点边界强制，不在节点内部代码强制**：`NodeHooks.check_cancelled`/`check_budget` 在每个节点执行前调用一次（`step_node`/`agent_node`/`human_node` 开头，来源：`lubanagent/graph/builder.py:240-241, 257-258, 277-278`），节点函数体内部若长时间运行不会被中途打断。
- **取消传播链**：父 `RunContext.metadata["cancellation_token"]` 若为 `CancellationToken` 实例，则节点边界调用 `token.throw_if_cancelled()`；若父级已取消，抛出的 `RunCancelledError`/`asyncio.CancelledError` 会被 `_finalize_on_error` 捕获并把图 Run 终态置为 `CANCELLED`（来源：`lubanagent/graph/runner.py:308-317, 256-261`）。
- **预算继承链（三级 fallback）**：① 父 `RunContext.metadata["budget_guard"]` 若是 `BudgetGuard` 实例则直接共用（成员消费累计进同一池）；② 否则若父 `RunContext.budget` 非空，用它新建一个 `BudgetGuard(timeout_seconds=300.0)`；③ 否则退回 `GraphRunner` 自身构造时传入的 `budget`；均无则 `guard=None`（不做预算/超时闸）（来源：`lubanagent/graph/runner.py:129-139`，`_GRAPH_TIMEOUT_SECONDS = 300.0` 定义于 `runner.py:49`）。
- **resume 的父级治理回接（风险 R2 修复）**：guard/token 是进程内对象、不进 LangGraph checkpoint，interrupt 时 `run()` 把已解析的 `(guard, token, user_id)` 按 `thread_id` 冻结进 runner 内存绑定表 `_parent_bindings`（`_ParentBinding` dataclass，`runner.py:67-73、95、112`）；`resume()` 按优先级重建父上下文（`runner.py:131-137` docstring）：**显式传入 `parent_ctx`**（跨进程重启 / 父 Run 已终结时由调用方持原 guard/token 重建）> **冻结绑定**（`_restore_parent_ctx`，`runner.py:184-205`，metadata 回填同一 guard/token 对象引用——同一预算池、同一取消链，预算消耗不重置、父级取消照常传播）> **现状兜底**（runner 自身 `budget`，无则不 guard）。Run 到终态（completed 或错误收口）即从绑定表清除（`runner.py:327、337`），不无限增长；跨进程重启后内存绑定丢失、usage 只存活在 guard 对象内，预算池无法续接，如实降级为兜底而非假恢复（`runner.py:7-11` 模块 docstring）。
- **agent 节点产生的子 Run 治理终态映射回图内异常**：子 Run 若非 `COMPLETED`，按 `end_reason` 分别映射为 `BudgetExceededError`/`RunTimeoutError`/`LubanError(INTERNAL_ERROR)` 抛出，交由图 Run 统一的 `_finalize_on_error` 收口（来源：`lubanagent/graph/runner.py:350-377`）。
- **终态 Run 拒绝重复 resume（H12 幂等边界）**：`resume()` 在锁内检查 `run.status ∈ _TERMINAL_STATUSES = {COMPLETED, FAILED, CANCELLED}`，命中抛 `LubanError(VALIDATION_ERROR)`，终态不被改写、已执行的节点副作用不重复执行；可 resume 的只有 `WAITING_APPROVAL`（含 `RUNNING` 兜底）。测试佐证 `tests/lubanagent/graph/test_resume_governance.py`（`test_repeated_resume_after_terminal_rejected`、`test_resume_twice_across_runner_instances`）。
- **同一线程并发 resume 经 per-thread 锁串行（H12）**：`_resume_locks: dict[str, asyncio.Lock]` 按 `thread_id` 常驻（与 checkpoint 生命周期同界，不在终态清理，避免并发等待者拿到不同锁对象导致串行穿崩）；整个 resume 体（checkpoint 存在性检查、终态检查、状态流转、`ainvoke`）都在锁内执行——先到者推进至终态，后到者撞终态显式拒绝，副作用恰执行一次（测试佐证 `test_parallel_resume_serialized_side_effect_once`）。
- **图 Run 是 `runs` 表的正式外键子记录，需要 stub Agent/AgentVersion 兜底**：因 `RunRow.agent_id`/`agent_version_id` 为非空外键，`_ensure_stub_agent` 会在首次执行某图时插入 `agents`（`key=graph:<flow>`）与 `agent_versions`（`version=0`）各一条记录，若已存在则跳过（来源：`lubanagent/graph/runner.py:149-182`）。
- **`flow_as_tool` 产物的 `args_schema` 只有单一 `input: string` 字段**，但 `additionalProperties: True`（来源：`lubanagent/graph/tool_adapter.py:30-34`）；`_ctx` 注入依赖 `ToolExecutor._accepts_ctx` 对 handler 签名的反射检测，仅当 handler 显式声明 `_ctx` 形参才生效（来源：`lubanagent/tool/executor.py:89-96, 244-246`）。
- **checkpointer 不支持并发跨事件循环复用同一连接**：sync 兼容层 `_run_blocking` 若检测到已在事件循环内，会开独立线程跑 `asyncio.run` 新事件循环，并用独立短生命周期 `Database(str(self._db.engine.url))`（来源：`lubanagent/persistence/checkpoint.py:260-289`，注释标注"LangGraph 兼容路径，非常用"）。
- **依赖方向硬约束（import-linter 守卫）**：`lubanagent.graph` 可消费 `runtime`/`model`/`tool`/`persistence`/`observability`/`extension`/`config`/`core` + `langgraph`/`langchain_core`；禁止依赖 `lubanagent.mcp`/`skill`/`knowledge`/`memory`/`multiagent`/`api`/`app`/`cli`（来源：`importlinter.toml:202-211`，契约名"分层：graph 可消费 runtime/model/tool（编排层），不依赖 api/app/mcp/能力件"）。反向：`runtime` 禁止依赖 `graph`（来源：`importlinter.toml:130-139`），保证 graph 是编排层而非运行时核心的依赖项。
- **用户不为普通 Agent 写图，图专用于确定性流程**（业务约定而非代码强制，来源：`docs/contracts/D3-4-graph.md:101`，对应 AGENTS.md §4"一个引擎两种用法"）。

## 名词

| 术语 | 语义 |
|---|---|
| GraphBuilder | 可变的图 DSL 构建器，链式方法（`.step/.agent/.human/.when/.entry/.edge`）收集节点与边定义，`.build()` 后产出不可变的 `CompiledFlow`（来源：`lubanagent/graph/builder.py:69-179`） |
| CompiledFlow | `build()` 的产物：`GraphBuilder` 拓扑校验通过后的冻结包装（`frozen=True` dataclass），保留原始 `builder` 供每次 `GraphRunner.run` 时重新编译为 LangGraph 图（来源：`builder.py:351-364`） |
| GraphRunner | 图执行器：负责子 Run 建档、逐节点 Trace 写入、interrupt/resume 驱动、取消与预算继承（来源：`lubanagent/graph/runner.py:61-63`） |
| GraphResult | `GraphRunner.run`/`.resume` 的统一返回类型：`status`（`"completed"` 或 `"interrupted"`）+ `output`（终态/当前 state）+ `interrupt`（中断 payload）+ `run_id` + `thread_id`（来源：`runner.py:52-59`） |
| Checkpoint / LubanCheckpointer | LangGraph 的执行快照机制；`LubanCheckpointer` 是本项目自研的 `BaseCheckpointSaver` 实现，落自有 `checkpoints` 表而非引入 langgraph 官方 Postgres/SQLite Saver（本体在 `lubanagent/persistence/checkpoint.py`，`graph.checkpoint` 为 re-export 垫片；来源：`persistence/checkpoint.py:1-4, 35-46`） |
| human 节点 | DSL 中 `.human(name, prompt=)` 定义的节点类型，执行时调用 LangGraph `interrupt()` 使图执行暂停，等待外部经 `GraphRunner.resume` 传入的值续跑（来源：`builder.py:105-108, 274-283`） |
| NodeHooks | 每次 `run`/`resume` 时由 `GraphRunner` 组装、注入编译期的节点执行钩子集合：`check_cancelled`/`check_budget`/`on_node_started`/`on_node_completed`/`run_agent`（来源：`builder.py:39-48`） |
| flow_as_tool | 把 `CompiledFlow` 包装为 `RegisteredTool` 的适配函数，是"三种结合形态"之三"流程 = Tool"的实现（来源：`tool_adapter.py:1, 14-59`） |
| agent_key=graph:\<flow_name\> / agent_version=0 | 图 Run 在 `runs` 表的标记约定：无真实 Agent 版本概念，用固定前缀 + `_STUB_VERSION=0` 占位，供 `agents`/`agent_versions` 外键约束满足（来源：`runner.py:48, 194-209`） |
| _FlowState | LangGraph 侧的内部 State Schema，仅一个字段 `data: dict[str, Any]`；用户态本身是纯 dict，包一层是因为 plain dict schema 在 langgraph 1.x 不会累积 state（来源：`builder.py:1-5, 23-24`） |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| persistence | `Database`、`RunRepository`、`CheckpointRow`、`AgentRow`/`AgentVersionRow` | `GraphRunner` 用 `Database.session()` 读写 `runs` 表；`LubanCheckpointer` 读写 `checkpoints` 表；`_ensure_stub_agent` 读写 `agents`/`agent_versions` 表（来源：`runner.py:16-37, 149-182`；`persistence/checkpoint.py:14-26`） |
| runtime | `AgentRuntime.run(RunRequest)`、`BudgetGuard`、`CancellationToken` | `.agent()` 节点经 `hooks.run_agent` 调 `AgentRuntime.run`（子 Run，`parent_run_id`/`budget_guard`/`user_id` 透传，`runner.py:358`）；预算/取消继承复用 `runtime` 的 `BudgetGuard`/`CancellationToken` 类型（来源：`runner.py:38-43, 350-360`） |
| extension | `Registry`/`default_registry`、`GraphDescriptor`、`RegisteredTool` | `CompiledFlow.to_graph_descriptor()` 产出 `GraphDescriptor` 供 `@graph`/`Registry.register_graph` 注册（来源：`builder.py:17-18, 300-302`；`lubanagent/extension/decorators.py:72-86`）；`flow_as_tool` 产出 `RegisteredTool`（来源：`tool_adapter.py:7-8`） |
| core | `ErrorCode`/`LubanError`/`Run`/`RunContext`/`RunStatus`/`Budget`/`BudgetExceededError`/`EndReason`/`TraceEvent` 等 | 错误类型、Run 生命周期实体、预算/取消/Trace 相关类型定义（来源：`runner.py:18-33`；`builder.py:17`） |
| observability | `TraceWriter` | `GraphRunner` 经 `TraceWriter(db)` 写入 `STEP_STARTED`/`STEP_COMPLETED`/`BUDGET_EXCEEDED` 等 Trace 事件（来源：`runner.py:35, 77, 264-266, 323-342`） |
| langgraph（第三方库） | `StateGraph`/`BaseCheckpointSaver`/`interrupt`/`Command`/`CompiledStateGraph` | DSL 编译目标与 checkpoint/中断机制的底层引擎（来源：`builder.py:12-15`；`persistence/checkpoint.py:15-23`；`runner.py:15`） |
| app/graphs/*（用户领地） | `@graph(name=..., description=...)` 装饰的构建函数（如 `app/graphs/refund_flow.py:97-99`） | 用户用 `GraphBuilder` DSL 定义具体业务流程（如退款流水线），本模块不 import 用户模块，只在运行期经 `Registry` 查找（来源：`docs/contracts/D3-4-graph.md:100` 边界契约；`app/graphs/refund_flow.py` 全文） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| multiagent | `CompiledFlow`/`GraphBuilder`/`GraphResult`/`GraphRunner`/`StepFn`（`from lubanagent.graph import ...`） | 多 Agent 编排（`TeamRunner`）把 supervisor/成员委派拓扑编译为 `CompiledFlow`，复用 `GraphRunner` 跑执行（不重写第二套调度器），checkpoint/中断/Trace/取消传播全部继承本模块（来源：`lubanagent/multiagent/team.py:1-6, 24, 241`） |
| tool（经 flow_as_tool） | `ToolExecutor.execute`/`execute_by_key` | 流程包装为 `RegisteredTool` 后，可被普通 Agent 经工具调用链路执行，`_ctx` 注入机制专为此场景设计（来源：`lubanagent/tool/executor.py:244-246` 注释"如 flow_as_tool 的 parent_ctx 预算继承/取消传播"） |
| persistence（runs/checkpoints/agents/agent_versions 表） | `RunRepository.update_run`、`session.merge(CheckpointRow(...))`、`session.add(AgentRow/AgentVersionRow(...))` | 每次图执行落一条 Run 记录、每个 checkpoint 落一行、首次执行某图时兜底写入 stub Agent/Version（来源：`runner.py:184-233, 149-182`；`persistence/checkpoint.py:170-198`） |
| app/graphs/*（用户领地） | `GraphBuilder` DSL、`@graph` 装饰器 | 用户在自己的目录（如 `app/graphs/refund_flow.py`）用本模块提供的 DSL 定义并注册确定性流程（来源：`app/graphs/refund_flow.py:1-11`） |
