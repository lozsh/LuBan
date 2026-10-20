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

# multiagent 模块

## 定位

`lubanagent/multiagent/` 是 LubanAgent 的多 Agent 协作层（一等卖点，非后期扩展，见 `AGENTS.md` 第 2 条第 3 款）：提供 supervisor 路由 / 子 Agent 委派拓扑（`lubanagent/multiagent/supervisor.py`）、MultiAgent DSL（`.supervisor().agent().route()`——`route()` 声明路由策略三项：`max_delegations` 委派上限 / `entry_member` 入口成员 / `fanout` 扇出并行，均可缺省零变化，`lubanagent/multiagent/team.py`）、团队共享状态（`lubanagent/multiagent/state.py`）。本模块无独立业务实体/表，编排语义落在 `Team`（拓扑定义，纯内存数据类）与 `TeamState`（图执行期共享 state）两个对象上；持久化复用 `lubanagent/persistence` 的 `Run`/`Trace` 表，编排 Run 记为 `agent_key="graph:team:<name>"` 的一条 `Run` 记录（来源：`lubanagent/multiagent/team.py:59, 352`、`lubanagent/graph/runner.py:196`）。

聚合根：`Team`（不可变拓扑，`lubanagent/multiagent/team.py:319-326`）。运行期入口：`TeamRunner`（`lubanagent/multiagent/team.py:329-405`）。

本模块依赖 `lubanagent/graph`（拓扑编译为 `CompiledFlow` 后跑 `GraphRunner`，不重写调度器）与 `lubanagent/runtime`（成员/supervisor 的每次决策、每次委派均是一次 `AgentRuntime.run` 子 Run）。用户领地示例：`app/multiagents/cs_team.py`（客服分流团队：cs-triage 分流 → cs-faq / cs-refund），经 `app/api/teams.py` 自定义路由 `POST /v1/teams/cs-team/runs` 暴露。

## 接口契约

### HTTP

本模块（框架领地）不内置 HTTP 路由；HTTP 暴露由用户领地按需实现。

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| POST | `/v1/teams/cs-team/runs` | 触发客服分流团队（用户领地示例路由，非框架内置） | 团队/成员 Agent 未落库 → `RUN_NOT_FOUND`；预算超限 → `BudgetExceededError`；取消 → `RunCancelledError`（均来源：`lubanagent/multiagent/team.py`、`lubanagent/graph/runner.py`） |

（来源：`app/api/teams.py:15-52`。框架领地本身不提供 REST 路由，`TeamRunner` 只是 Python API。）

### RPC / MQ / Cron

无。

### Python API（DSL 方法链 + 运行入口）

本模块对外契约以 Python 类/方法为主，非 HTTP/RPC，单列如下：

| 类 | 方法 | 签名 | 说明 |
|---|---|---|---|
| `TeamBuilder`（`lubanagent/multiagent/team.py:220-316`） | `__init__` | `(name: str, *, description: str = "")` | 创建构建器 |
| | `.supervisor(agent_key: str) -> TeamBuilder` | 设置 supervisor Agent key（可链式） | `team.py:233-235` |
| | `.agent(agent_key: str, *, description: str = "") -> TeamBuilder` | 登记一个成员（可多次调用）；`description` 为路由提示，DB 中 Agent 定义描述优先覆盖（`team.py:392-405`） | `team.py:237-243` |
| | `.route(*, max_delegations: int = 8, entry_member: str \| None = None, fanout: bool = False) -> TeamBuilder` | 路由策略声明（均可缺省 = 默认 supervisor 直接纳管，行为零变化）：`max_delegations` 委派上限（防 ping-pong 硬上限，按成员执行次数计——扇出一轮 k 个成员 = k 次，入口成员执行同样计入）；`entry_member` 指定入口成员（用户消息先进该成员，其输出回 supervisor 决策后续）；`fanout` 启用扇出并行（supervisor 可一次决策委派多成员并行，见「行为」） | `team.py:244-262` |
| | `.build() -> Team` | 校验拓扑（见「边界规则」）并编译为 `Team`（内含 `CompiledFlow`；声明 `entry_member` 时入口节点为 `entry-member`，边 `entry-member → supervisor`） | `team.py:264-316` |
| `Team`（`lubanagent/multiagent/team.py:319-326`，`@dataclass(frozen=True)`） | 只读字段 | `name: str`、`description: str`、`supervisor_key: str`、`member_keys: list[str]`、`flow: CompiledFlow`、`entry_member: str \| None = None`、`fanout: bool = False` | 不可变拓扑产物 |
| `TeamRunner`（`lubanagent/multiagent/team.py:329-405`） | `__init__` | `(*, db: Database, runtime: AgentRuntime, registry: Registry \| None = None)` | 薄封装 `GraphRunner` |
| | `.run(team: Team, user_input: str, *, thread_id: str \| None = None, parent_ctx: RunContext \| None = None) -> GraphResult` | 触发一次编排 Run；`thread_id` 缺省自动生成；`parent_ctx` 用于父子预算/取消传播 | `team.py:339-390` |
| `SupervisorRouter`（`lubanagent/multiagent/supervisor.py`） | `__init__` | `(*, agent_key: str, members: list[str], max_delegations: int = 8, fanout: bool = False)` | 供 `TeamBuilder.build()` 内部构造，用户不直接实例化 |
| | `.decide(state: TeamState, *, runtime: AgentRuntime, parent_ctx: RunContext) -> SupervisorDecision` | 一轮路由决策（supervisor 子 Run + 结构化输出解析） | `supervisor.py:98-120` |
| | `.agent_key` / `.members` / `.max_delegations` | 只读属性 | `supervisor.py:69-84` |

返回类型：`SupervisorDecision`（`action: Literal["delegate","answer"]`、`agent: str \| None`、`agents: list[str] \| None`（扇出并行目标，去重保序，仅 fanout 启用且全部命中成员白名单时非空）、`input: str`、`answer: str \| None`、`reason: str \| None`，`supervisor.py:40-47`）；`GraphResult`（`status: "completed"\|"interrupted"`、`output: dict`、`interrupt: dict \| None`、`run_id: str`、`thread_id: str`，定义于 `lubanagent/graph/runner.py:52-58`）。

## 数据模型

本模块不新增数据库表，复用 `lubanagent/persistence` 的 `runs`/`trace_events` 表存编排 Run 与节点流转（`agent_key` 前缀 `graph:team:<name>` 标记编排 Run，来源：`lubanagent/graph/runner.py:184-209`）；成员/supervisor 的每次子 Run 各自落一条常规 `Run` 记录，`agent_key` 为其自身 Agent key（`lubanagent/multiagent/team.py:115-139, 158-182`、`supervisor.py:108-120`）。

模块内的两个数据结构是**运行期内存对象**，不落库（随图 checkpoint 一并序列化，`LubanCheckpointer` 本体在 `lubanagent/persistence/checkpoint.py`，`graph.checkpoint` 为 re-export 垫片，由 persistence 模块负责）：

### `DelegationRecord`（`lubanagent/multiagent/state.py:8-14`，`pydantic.BaseModel`）

一次委派的留痕。

| 字段（代码类型） | 非空 | 说明 |
|---|---|---|
| `agent_key: str` | ✓ | 被委派的成员 Agent key |
| `input: str` | ✓ | supervisor 转述给成员的输入 |
| `output: str` | ✓ | 成员子 Run 的输出文本 |
| `run_id: str` | ✓ | 成员子 Run 的 `Run.id`（可据此在 `runs` 表回查完整治理记录） |

### `TeamState`（`lubanagent/multiagent/state.py:17-27`，`TypedDict, total=False`）

团队编排的共享 state，承载于图 state 的 `data` 键下（纯 dict 合并语义，无索引/无表）。

| 字段（代码类型） | 说明 |
|---|---|
| `input: str` | 用户原始请求 |
| `messages: list[dict[str, str]]` | 简化对话轨迹，元素 `{"role": "supervisor"\|<agent_key>, "content": str}`，供 supervisor 路由 prompt 拼接对话历史 |
| `delegations: list[DelegationRecord]` | 已发生的委派记录累积（graph state 合并语义为 list 整体替换，见 `_member_node` 每次读旧列表 append 后整体写回，`team.py:172-181`） |
| `final_output: str \| None` | 最终答复（`finalize` 节点写入） |

运行期还会携带 `thread_id`、`decision`（`SupervisorDecision.model_dump()`）、`member_descriptions` 等内部键，这些键**不属于本 schema**（`state.py:21-22` 注释明确排除）。

索引：无（非数据库表）。

## 行为

### 状态机

无独立业务状态机；编排 Run 的生命周期状态复用 `lubanagent/core` 的 `RunStatus`/`EndReason`（`RUNNING → COMPLETED/FAILED/CANCELLED`），由 `lubanagent/graph/runner.py` 统一驱动，`multiagent` 模块不额外定义状态字段。

团队内部的**图节点流转**（非业务状态机，是 LangGraph 拓扑，来源：`lubanagent/multiagent/team.py:44-47, 264-316`）：

```
[未声明 entry_member]
supervisor（决策节点，_supervisor_step）
  ├─ 条件边 _is_delegate(state) == True  →  member（成员执行节点，_member_node）
  │                                            └─ 边：member → supervisor（回到决策节点，形成委派循环）
  └─ 条件边 _is_delegate(state) == False →  finalize（汇总节点，_finalize_node，自动接 END）

[声明 entry_member（route(entry_member=...)）]
entry-member（入口成员节点，_entry_member_step，用户消息先进该成员）→ supervisor → （同上循环）
```

- 入口缺省为 `supervisor` 节点（`builder.entry(_SUPERVISOR_NODE)`）；声明 `entry_member` 时入口改为 `entry-member` 节点并加边 `entry-member → supervisor`（`team.py:293-298`）。
- 循环终止条件二选一：① supervisor 决策 `action=="answer"` → 走向 `finalize`；② 委派轮次达 `max_delegations` → `SupervisorRouter._forced_answer` 直接返回 `action="answer"`（不再调模型，`supervisor.py:124-130`），下一轮条件边同样判为非 delegate → `finalize`。
- fanout 扇出（`route(fanout=True)` 声明，默认关）：supervisor 一次决策可委派多成员（`{"action": "delegate", "agents": [...]}`，去重保序），member 节点内 `asyncio.gather` 并行执行、结果按声明顺序合并后回 supervisor；未启用时 `agents` 列表不进解析分支（prompt 也不提供该语法）→ 老团队行为零变化（`supervisor.py:148-160, 188-193`）。选型理由：图 state 是 `{"data": dict}` 单通道（无 reducer），LangGraph 原生并行边会互相覆盖 state 更新，gather 在单节点内合并一次 state 更新（`team.py:10-15` 模块 docstring）。

### 调用树

#### 一次编排 Run（`TeamRunner.run`，以 `app/multiagents/cs_team.py` 的 cs-team 拓扑为例：supervisor=cs-triage，成员=cs-faq/cs-refund）

用户/业务系统调 `TeamRunner.run(team, user_input, thread_id=..., parent_ctx=...)`；内部通过模块级 `ContextVar`（`_current_invocation`，`team.py:70-72`）把 `runtime`/`db`/`graph_key`/`guard` 注入给图节点闭包（不进 state，不影响 checkpoint 序列化），再委托 `GraphRunner.run` 跑编译好的 `CompiledFlow`。前置限制：`team.flow` 须来自 `TeamBuilder.build()`（已通过拓扑校验）；副作用：产生 1 条 `graph:team:<name>` 编排 Run 记录 + N 条 supervisor/成员子 Run 记录，全部写 Trace（`STEP_STARTED`/`STEP_COMPLETED`，来源：`lubanagent/graph/runner.py:323-342`）。

```
TeamRunner.run(team, user_input, thread_id, parent_ctx)
├── _resolve_guard(parent_ctx)  # 预算继承链：父 guard 共享 → 父 budget 自建 → None（team.py:375-390）
├── _member_descriptions(team)  # 路由提示为底、DB Agent 定义描述覆盖（team.py:392-405）
├── _current_invocation.set(invocation)  # contextvar 注入运行期服务
└── GraphRunner.run(team.flow, initial_state, thread_id, parent_ctx)
    ├── 落编排 Run（agent_key="graph:team:cs-team"，parent_run_id=parent_ctx.run_id）
    ├── [声明 entry_member 时先走 entry-member 节点] _entry_member_step
    │   └── runtime.run(RunRequest(agent_key=<入口成员>, ...)) → DelegationRecord 追加 → 边：entry-member → supervisor
    ├── [supervisor 节点] _supervisor_step → SupervisorRouter.decide(state, runtime, parent_ctx)
    │   ├── 委派轮次达上限 → _forced_answer()  # 不调模型，reason="max_delegations"
    │   └── 否则 → runtime.run(RunRequest(agent_key="cs-triage", ...))  # supervisor 子 Run
    │       └── _parse(raw)  # 截取 {...} JSON 块解析；失败/未知成员/无效扇出列表 → 降级 answer（不炸编排）
    ├── [条件边] _is_delegate(state)
    │   ├── True → [member 节点] _member_node（目标 = 扇出 agents 列表或单 agent，_decision_targets）
    │   │   └── asyncio.gather(*(_run_member(invocation, key, input, thread_id) for key in targets))
    │   │       └── runtime.run(RunRequest(agent_key=<cs-faq|cs-refund>, parent_run_id=编排Run.id, budget_guard=共享guard))
    │   │       └── ensure_child_ok(child)  # 子 Run 非 COMPLETED → 映射为 BudgetExceededError/RunTimeoutError/RunCancelledError/LubanError
    │   │       → 按声明顺序逐个 DelegationRecord 追加到 state["delegations"]，边：member → supervisor（回到决策节点）
    │   └── False → [finalize 节点] _finalize_node → final_output = decision.answer 或最近一次委派输出
    └── 落编排 Run 终态（COMPLETED/FAILED/CANCELLED）+ Trace 收尾
```

扇出并行的治理同路性：并行成员子 Run 共享同一 `BudgetGuard` 实例（消费同池累计）、取消传播走节点边界 + 外层任务取消、Trace 父子（`parent_run_id`=编排 Run）与单成员委派完全同一条代码路径（`_run_member` 单构造函数共用，`team.py:142-155`）。

（来源：`lubanagent/multiagent/team.py:101-204, 244-262, 329-390`、`lubanagent/multiagent/supervisor.py:98-172`、`app/multiagents/cs_team.py:9-19`。测试佐证：委派 FAQ `tests/lubanagent/multiagent/test_routing.py:9-53`、委派退款 `test_routing.py:56-80`、直接作答 `test_routing.py:83-99`、JSON 解析失败降级 `test_routing.py:102-114`；路由策略三项能力（入口成员/扇出/上限计入）`tests/lubanagent/multiagent/test_routing_policy.py`。）

## 边界规则

- **拓扑校验（构建期硬约束）**：`TeamBuilder.build()` 强制 (1) 必须先调 `.supervisor()` 设置 supervisor，否则 `VALIDATION_ERROR`；(2) 至少登记 1 个成员（`.agent()`），否则 `VALIDATION_ERROR`；(3) 成员列表不可包含 supervisor 自身 key，否则 `VALIDATION_ERROR`；(4) 声明的 `entry_member` 必须是已登记成员，否则构建期 `VALIDATION_ERROR`（"入口成员必须是已登记成员"，来源：`lubanagent/multiagent/team.py:264-283`；测试：`tests/lubanagent/multiagent/test_builder.py:9-24`、`test_routing_policy.py`）。
- **委派轮次上限（防 ping-pong）**：`max_delegations`（默认 8，经 `.route(max_delegations=...)` 可配）由代码强制而非依赖模型自律，**按成员执行次数计**——扇出一轮 k 个成员 = k 次，入口成员执行同样计入（不可借入口绕过上限防 ping-pong）；`delegations` 数达上限时 `SupervisorRouter.decide` 直接返回强制 `answer`（`reason="max_delegations"`），**不再调用模型**，避免委派死循环无限消耗预算（来源：`lubanagent/multiagent/supervisor.py:105-107, 124-130`；测试：`tests/lubanagent/multiagent/test_pingpong.py:8-31` 验证触顶后 supervisor 子 Run 恰为 `max_delegations` 次，不多不少）。此为唯一的"调用深度/循环保护"机制；本模块**不做**递归深度限制（成员 Agent 内部若再发起委派超出本模块职责范围，`[TODO: 未发现嵌套 Team 场景的证据，当前仅支持单层 supervisor-成员拓扑]`）。
- **委派目标合法性**：`SupervisorRouter._parse` 只信任 JSON 中 `agent`/`agents` 字段命中 `self._members` 白名单的委派；未知成员、以及扇出列表含未知成员或为空列表，一律降级为 `answer`（`reason="unknown_agent"`），不会把请求转发到未登记的 Agent（来源：`supervisor.py:132-172`）。扇出仅在 `fanout=True` 时进解析分支（去重保序：同一成员一轮扇出只执行一次）。
- **成员子 Run 终态收口**：成员/supervisor 子 Run 非 `COMPLETED` 时，`ensure_child_ok` 按 `end_reason` 映射为对应异常并向上抛出，由编排 Run（图 Run）统一映射终态，不在节点内静默吞掉（来源：`supervisor.py:43-58`、`team.py:118-134`，与 `lubanagent/graph/runner.py:361-377` 的 agent 节点同一套映射规则）。
- **编排 Run 与会话 Run 的预算继承**：子 Run（supervisor 决策 Run、成员执行 Run）**不另开预算额度**，继承路径为：`parent_ctx.metadata["budget_guard"]` 共享 guard（存在则直接复用同一个 `BudgetGuard` 实例）→ 否则若 `parent_ctx.budget` 存在则自建一个新 `BudgetGuard`（超时兜底 300 秒）→ 否则不设 guard（来源：`lubanagent/multiagent/team.py:278-292`，与 `lubanagent/graph/runner.py:129-139` 的 `GraphRunner._resolve_guard` 同一套算法）。共享 guard 下，supervisor 每轮 decide 与每次成员委派的消费**累计进同一个池**，任一次超支即让整条编排 Run 以 `BUDGET_EXCEEDED` 熔断，不允许"子 Run 烧完额度父 Run 继续跑"（测试证据：`tests/lubanagent/multiagent/test_governance.py:34-58` 验证撞线熔断且 `guard.usage.total` 精确累计；`test_governance.py:61-84` 验证未撞线时 3 次模型调用的用量全部计入同一 guard）。
- **取消传播**：`parent_ctx.metadata["cancellation_token"]` 存在时，每个图节点边界（`check_cancelled`，`lubanagent/graph/runner.py:315-317`）都会检查并在父 token 已取消时抛 `RunCancelledError`，成员子 Run 在此边界之前不会被创建（测试证据：`tests/lubanagent/multiagent/test_governance.py:87-105` 验证父 token 起跑前即取消 → 编排 Run 直接 `CANCELLED`；`test_governance.py:108-146` 验证 supervisor 决策完成后取消 → 成员节点边界拦下，成员子 Run 未产生 `faq_runs == []`）。
- **`user_id` 观测标签继承**：`_TeamInvocation` 携带 `parent_ctx.user_id`（透传观测标签，无用户管理语义），编排 Run（图 Run）、supervisor 决策子 Run 与成员子 Run 全部继承同一 `user_id`，整条编排链可按用户排查（来源：`lubanagent/multiagent/team.py:49,102,132,261`、`supervisor.py:97`）。
- **Run 树形只靠 `parent_run_id`**：Run 模型没有也不需要 `root_run_id` 字段——子 Run 携带 `parent_run_id`（`core/run.py:52`、`persistence/models/run.py:22`），根 Run 沿 `parent_run_id` 链逐级回溯，成本归集按它 BFS 逐层推导（`observability/cost/report.py:72`）。技术方案 §10.6 原先"携带 `parent_run_id` 与 `root_run_id`"的字段落差已修正（2026-10-15，文档对齐代码）。
- **路由提示描述优先级**：`.agent(agent_key, description=...)` 传入的 `description` 只是路由提示的兜底值；若该 Agent 在 DB 中有正式定义（`AgentRepository.get_agent`），则**用 DB 定义的 `description` 覆盖**路由提示（来源：`lubanagent/multiagent/team.py:295-305` `_member_descriptions` 方法，"取自定义库为准"）。

## 名词

| 术语 | 语义 |
|---|---|
| 编排 Run | `agent_key` 形如 `graph:team:<name>` 的 `Run` 记录，代表一次 `TeamRunner.run` 调用的整体图执行；是会话 Run 的子 Run（若带 `parent_ctx`），同时是 supervisor/成员子 Run 的父 Run（来源：`lubanagent/graph/runner.py:184-209`、`lubanagent/multiagent/team.py:51-67`） |
| supervisor | 拓扑中负责路由决策的 Agent（`TeamBuilder.supervisor()` 指定的 `agent_key`），每轮决策本身是一次子 Run（`agent_key`=该 Agent 自身 key，非 `graph:team:*`），产出 `SupervisorDecision`（来源：`supervisor.py:61-102`） |
| 成员（member） | 被 supervisor 委派执行具体任务的 Agent（`TeamBuilder.agent()` 登记），每次委派是一次独立子 Run（来源：`team.py:142-182`） |
| 委派（delegate） | supervisor 决策 `action=="delegate"` 时把请求转述给某个（扇出启用时为多个）成员执行的动作，产出 `DelegationRecord`（来源：`state.py:8-14`、`team.py:158-182`） |
| 强制收尾（forced answer） | 委派轮次达 `max_delegations` 时代码强制把决策改写为 `answer`（不再调模型），`reason` 标记为 `"max_delegations"`（来源：`supervisor.py:124-130`） |
| 入口成员（entry member） | `route(entry_member=...)` 指定的第一个处理用户消息的成员（`entry-member` 节点，用户消息直进成员、输出回 supervisor 决策后续）；执行计入委派上限，须为已登记成员（否则构建期 `VALIDATION_ERROR`）（来源：`team.py:185-204, 279-283`） |
| 扇出（fanout） | `route(fanout=True)` 启用的并行委派：supervisor 一次决策委派多成员（`{"action":"delegate","agents":[...]}`，去重保序），member 节点内 `asyncio.gather` 并行执行、结果按声明顺序汇总（预算共享同池累计/取消传播/Trace 父子与单成员委派同路径）（来源：`team.py:158-182`、`supervisor.py:148-160`） |
| 路由提示（member description hints） | `.agent(description=...)` 传入的、供 supervisor 路由 prompt 展示的成员简介；会被 DB 中该 Agent 的正式 `description` 覆盖（来源：`team.py:237-243, 392-405`） |
| `_TeamInvocation` | 一次编排 Run 的运行期服务容器（`runtime`/`db`/`graph_key`/`guard`），经模块级 `ContextVar` 注入图节点闭包，仅在 `TeamRunner.run` 生命周期内有效（来源：`team.py:53-95`） |

## 上下游

**上游依赖**（本模块消费外部数据/服务）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent/graph` | `GraphBuilder`（`.step()/.entry()/.when()/.edge()`）、`GraphRunner.run()` | 团队拓扑编译为 `CompiledFlow`；实际图执行、子 Run 建档、逐节点 Trace、预算/取消传播全部委托给 `GraphRunner`，本模块不重写调度器（来源：`team.py:1-10, 204-221, 261-272`） |
| `lubanagent/runtime` | `AgentRuntime.run(RunRequest)` | supervisor 每轮决策与每次成员委派都是一次 `AgentRuntime.run` 调用（子 Run，`parent_run_id`=编排 Run id，`budget_guard` 透传共享池）（来源：`supervisor.py:108-120`、`team.py:142-155`） |
| `lubanagent/persistence` | `AgentRepository.get_agent()`、`RunRepository.list_runs()` | 读取成员/supervisor 的正式 Agent 定义描述（覆盖路由提示）；按 `(graph_key, thread_id)` 反查编排 Run id 供子 Run 挂 `parent_run_id`（来源：`team.py:51-67, 295-305`） |
| `lubanagent/core` | `RunContext`、`Budget`/`BudgetExceededError`/`RunCancelledError`/`RunTimeoutError`、`EndReason`/`RunStatus` | 预算/取消/超时的领域类型与异常语义，子 Run 终态映射依赖这些类型（来源：`supervisor.py:15-25`、`team.py:17-22`） |
| `lubanagent/extension` | `Registry` | `TeamRunner` 透传给内部 `GraphRunner`，供图节点解析用户扩展（来源：`team.py:23, 236-241`） |
| `app/multiagents/cs_team.py`（用户领地） | `TeamBuilder(...).supervisor().agent().route().build()` | 客服分流团队的具体拓扑定义（supervisor=cs-triage，成员=cs-faq/cs-refund，`max_delegations=4`），是本模块 DSL 的真实调用示例，非本模块代码但交叉验证契约行为（来源：`app/multiagents/cs_team.py:9-19`） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent/runtime` → `lubanagent/model` | `AgentRuntime.run()` 内部模型调用 | 每轮 supervisor 决策、每次成员委派最终都会触发一次真实/fake 模型调用（经四槽位大脑映射解析），消耗计入共享 `BudgetGuard`（来源：`supervisor.py:108-120`、`tests/lubanagent/multiagent/conftest.py:151-195`） |
| `lubanagent/observability`（Trace） | `TraceWriter.emit/start_run/finish_run`（经 `GraphRunner` 间接触发） | 编排 Run 的每次节点流转（`STEP_STARTED`/`STEP_COMPLETED`）与终态收尾写入 Trace，供整体回放（来源：`lubanagent/graph/runner.py:323-342`，本模块自身不直接写 Trace，经 `GraphRunner` 代劳） |
| `app/api/teams.py`（用户领地路由） | `POST /v1/teams/cs-team/runs` → `TeamRunner(db=ctx.db, runtime=ctx.runtime, registry=ctx.registry).run(team, ...)` | 用户领地把 `TeamRunner` 包装为 HTTP 触发入口，是本模块面向业务系统的实际暴露方式（框架本身不内置该路由）（来源：`app/api/teams.py:40-52`） |
