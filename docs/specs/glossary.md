<!--
TEMPLATE-LOCK: 本文件章节锁定为以下清单，禁止新增/改名/调序：
1. 跨模块术语
只收录两个及以上模块都要用到的全局业务概念；单模块概念写 modules/{module}.md 的「名词」节。
-->

# 跨模块术语 — LubanAgent

## 跨模块术语

| 术语 | 当前语义 |
|---|---|
| BrainProfile | 逻辑大脑四槽位枚举 `fast`/`smart`/`cheap`/`vision`，定义在 `lubanagent/core/brain.py`，解耦 Agent 定义与具体模型；`model` 模块用它解析出 `ModelDefinition`，`runtime`/`persistence`/`api` 均直接引用，切换模型只改库映射不改代码不发版。 |
| Registry / default_registry | `lubanagent/extension/registry.py` 定义的框架运行期唯一注册表，方法为单数形式（`register_tool`/`register_graph`/`register_hook`/`register_auth`/`register_knowledge`/`register_observability`）；`@tool`/`@graph`/`@hook` 装饰器在 `discover()` 扫描时自动调用。框架只查表不 import 用户模块，是 `lubanagent/* → app/*` 依赖方向禁令的核心解耦点。 |
| ToolDescriptor / ToolContract / RiskLevel | 工具注册描述与执行契约，定义在 `lubanagent/core/tool.py`；`RiskLevel`（low/medium/high）是触发人工审批的判据。`extension` 装饰器生产、`tool` 模块的 `ToolExecutor` 消费、`mcp`/`runtime`/`persistence` 均引用。 |
| Run / RunContext / RunStatus | 一次 Agent 执行的完整记录与状态机，定义在 `lubanagent/core/run.py`，落库为 `runs` 表；`runtime`/`graph`/`multiagent`/`observability`/`api` 几乎全部模块都以 `Run` 为核心锚点实体。 |
| 编排 Run / 子 Run | `parent_run_id` 建立的运行层级：`multiagent`/`graph` 的编排 Run 是会话 Run 的子 Run，supervisor 每轮决策、成员每次委派各自产生子 Run；预算（`Budget`/`BudgetGuard`）与取消（`CancellationToken`）沿此链路继承/传播，`runtime`/`graph`/`multiagent` 三模块共用同一套继承算法。 |
| Trace / TraceEvent / TraceEventType | 一次 Run 的观测事件流模型，定义在 `lubanagent/core/trace.py`，落库为 `traces`/`trace_events` 表（1:1 关联 `runs`）；`observability-trace-cost`（写入）与 `observability-adapters`（外发）是主要消费方，`api`/`persistence` 也直接引用。 |
| ObservabilityBackend / 降级为 noop | `lubanagent/extension/protocols.py` 定义的可插拔观测后端 Protocol（`start_trace`/`emit`/`end_trace`）；`OBSERVABILITY_BACKEND` env 选址，缺 key 时自动降级为 `noop`。外部后端故障不得阻断核心 Run 执行，失败经 `SafeBackend` 隔离并落 `export_failures` 表供重试（该表目前只写不读）。 |
| Checkpoint / LubanCheckpointer | LangGraph 执行快照的自研落库实现，对应 `checkpoints` 表；`graph` 模块定义，`multiagent` 复用同一个 `GraphRunner` 因此共享此机制，不重写第二套调度器。 |
| LubanError / ErrorCode | 定义在 `lubanagent/core/errors.py` 的统一错误体系，是全仓库异常处理的公共契约，`api` 模块负责 HTTP 错误码映射。 |
| Prompt 库 / 双轨引用 | Prompt 是独立版本化的一等实体（`core/prompt.py` 的 `PromptDefinition`/`PromptVersion`，落库为 `prompts`/`prompt_versions` 表）；Agent 定义经 `prompt_key`/`prompt_version` 引用（空 = 纯内嵌存量路径）。`persistence` 提供 `resolve_prompt_reference` 落库前解析（全文写入 `instructions` 落进 Agent 版本快照，运行期不回查 Prompt 库），`api` 的 agents 创建/发版端点接入，`ui` 的 Prompt 库页与 Agent 编辑器双轨消费。Prompt 新版不自动作用于引用方——Agent 吃新版必须显式产生新 Agent 版本。 |
| Budget / BudgetGuard | 运行预算三道闸（token/cost/时长），定义在 `lubanagent/core/budget.py`，`runtime/budget_guard.py` 实现具体熔断逻辑；`graph`/`multiagent` 的编排执行沿用同一套预算继承机制，`observability-trace-cost` 消费其结果做成本核算。 |
| 装配根（create_app） | 特指两个同名但不同领地的函数：框架侧 `lubanagent/app/factory.py::create_app()`（装配路由/扩展点/observability）与用户侧 `app/main.py::create_app()`（在框架基础上手动注册 Skill/知识源/自定义路由）。理解 LubanAgent 的启动流程需先分清二者。 |
| AuthProvider / Principal | `lubanagent/extension/protocols.py` 定义的鉴权扩展点 Protocol；用户可在装配根注册自定义实现替换框架内置 API Key 鉴权，`Principal` 是鉴权通过后的主体结果对象，框架中立、不绑定具体鉴权方案。 |
| 渠道层三扩展点（ChannelConnector / SessionStateStore / OutputGuard） | 定义在核心 `lubanagent/extension/protocols.py`，实现在商用独立模块 `lubanagent-channels/`（不进开源仓库）；核心不依赖渠道层，渠道层单向依赖核心，是开源核心与商用渠道层的边界契约。 |
| .lubanagent-upstream 三类领地标记（framework / scaffold-seed / user-owned） | 仓库根 `.lubanagent-upstream` 清单文件对文件/目录的分类：`framework`（`lubanagent/`，上游持续追踪）、`scaffold-seed`（客服示例种子文件，上游仅破坏性变更才动，用户原地改会冲突）、`user-owned`（用户新增文件，上游永不触碰）。`app-user-land`/`core-extension-config` 等模块的升级安全性都依赖这套分类。 |
| BIZ_BASE_URL | `lubanagent.config.Settings.biz_base_url` 对应的环境变量，`app-user-land` 模块的客服 Tool/知识源用它定位独立进程 Mock 业务服务地址（缺省 `http://localhost:9100`），是"`app`/`lubanagent` 不得反向依赖 `demos/`"这条依赖方向约束的具体落地机制。 |
