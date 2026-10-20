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

# core-extension-config 模块

## 定位

本模块合并三个紧密关联的框架领地子包，是 LubanAgent 的最底层无外部依赖契约层：

- `lubanagent/core/`（`lubanagent/core/__init__.py:1`）：纯数据层领域模型与统一错误体系，仅依赖 pydantic 与标准库，是 Agent 定义（`AgentDefinition`/`AgentVersion`）、Prompt 库（`PromptDefinition`/`PromptVersion`，`prompt.py`）、运行记录（`Run`/`RunContext`）、Trace 事件（`Trace`/`TraceEvent`/`Step`）、逻辑大脑槽位（`BrainProfile`）、模型注册（`ModelDefinition`/`ModelCapability`）、工具契约（`ToolDescriptor`/`ToolContract`）、预算（`Budget`）、Skill 元数据（`SkillMeta`/`SkillPackage`）、错误体系（`LubanError`）的唯一权威定义处；并自 H07 起提供共享脱敏组件 `redact.py`（字段级秘密键规则 + 脱敏展示副本构造，供 runtime/tool/observability 各外发边界复用）。import-linter 契约钉死 `core` 是最底层，禁止依赖任何兄弟包（`importlinter.toml:25-36`）。
- `lubanagent/extension/`（`lubanagent/extension/__init__.py:1`）：框架与用户领地的桥梁，定义 `ToolProvider`/`GraphProvider`/`AuthProvider`/`ObservabilityBackend`/`Hook`/`KnowledgeConnector`/`ChannelConnector`/`SessionStateStore`/`OutputGuard` 等 Protocol（`protocols.py`），提供运行期 `Registry`（`registry.py`）与 `@tool`/`@graph`/`@hook` 注册装饰器 + `discover()` 包扫描（`decorators.py`）。import-linter 钉死 `extension` 只依赖 `core`（`importlinter.toml:38-49`）。
- `lubanagent/config/`（`lubanagent/config/__init__.py:1`）：环境化配置加载，`Settings`（pydantic-settings，`.env` 分层）、`get_settings()` 进程级单例、`configure_logging()` 日志配置、`defaults.py` 零配置兜底常量。import-linter 钉死 `config` 只依赖 `core`（`importlinter.toml:51-62`）。

三者共同构成框架"地基"：`core` 定义契约类型，`extension` 定义用户如何挂入框架，`config` 定义框架如何从环境加载自身行为参数；上层所有模块（`persistence`/`runtime`/`api`/`tool`/`model` 等）都以此为基础构建。

## 接口契约

本模块不是 HTTP/RPC 业务模块，不暴露 HTTP/RPC/MQ/Cron 接口。对外契约是 **Python 公开 API**（类/函数签名、Protocol 定义），供框架内部模块与用户领地代码（`app/*`）导入使用。

### HTTP / RPC / MQ / Cron

无（本模块不暴露 HTTP/RPC 接口，对外契约见下方 Python API 契约）。

### Python API 契约

#### `lubanagent/core`（领域模型，全部为 pydantic `BaseModel`/`Enum`，构造失败即 pydantic `ValidationError`）

| 类型 | 名称 | 说明 | 错误情况 |
|---|---|---|---|
| Enum(str) | `AgentStatus`（`definition.py:13`） | Agent 生命周期状态：`draft`/`published`/`deprecated` | 非法枚举值构造报 `ValidationError` |
| Model | `IOContract`（`definition.py:19`） | Agent 输入输出契约：`input_type`/`output_schema`/`streaming` | — |
| Model | `BrainConfig`（`definition.py:27`） | 大脑槽位配置：`profile`/`fallback_profiles`/`allow_run_override` | — |
| Model | `KnowledgeConfig`（`definition.py:35`） | 知识检索配置：`sources`/`top_k`/`cite` + 防错答守门三字段 `score_threshold`（默认 `0.0`，不过滤）/`no_hit_fallback`（默认 `None`，不短路拒答）/`max_age_days`（默认 `None`，不过滤）（`definition.py:39-42`） | — |
| Model | `MemoryConfig`（`definition.py:45`） | 记忆配置：`session`/`window_size`/`summary`/`long_term` | — |
| Model | `BoundaryConfig`（`definition.py:52`） | 运行边界：`max_steps`/`timeout_seconds`/`budget`/`allow_human_handoff` | — |
| Model | `AgentDefinition`（`definition.py:61`） | Agent 定义本体（库内记录），含 Prompt 库双轨引用字段 `prompt_key`/`prompt_version`（`definition.py:73-74`），`tool_signatures` 用于版本冻结校验 | — |
| Model(frozen) | `AgentVersion`（`definition.py:88`） | 发布即冻结的不可变版本快照，`model_config = ConfigDict(frozen=True)` | 冻结后再赋值抛 pydantic 异常 |
| Model | `PromptDefinition`（`prompt.py:8`） | Prompt 库定义本体（当前版视图）：`key`/`name`/`description`/`content`/`variables`/`version`，`content` 即 Agent `instructions` 的来源文本 | — |
| Model(frozen) | `PromptVersion`（`prompt.py:19`） | Prompt 发布即冻结的不可变版本快照（与 `AgentVersion` 同型）：`prompt_key`/`version`/`content`/`variables`/`created_at` | 冻结后再赋值抛 pydantic 异常 |
| Enum(str) | `BrainProfile`（`brain.py:6`） | 逻辑大脑槽位：`fast`/`smart`/`cheap`/`vision`，切换模型=改库映射不改代码 | — |
| Model | `ModelCapability`（`model.py:8`） | 模型能力清单：`chat`/`streaming`/`tool_calling`/`structured_output`/`vision`/`reasoning`/`context_window`/`max_output` | — |
| Model | `ModelDefinition`（`model.py:21`） | 模型注册记录：`key`/`provider`/`model_name`/`factory`/`capability`/`params`/`base_url`/`api_key_env`/`enabled`，不存密钥本体 | — |
| Model | `Budget`（`budget.py:6`） | 运行预算上限：`max_tokens`/`max_cost`/`max_seconds`，任一为 `None` 表示不限 | — |
| Model | `TokenEstimate`（`budget.py:14`） | 调用前 token 估算：`input_tokens`/`output_tokens`，`total` 为计算属性 | — |
| Enum(str) | `RunStatus`（`run.py:13`） | 运行状态机：`pending`/`running`/`waiting_approval`/`completed`/`failed`/`cancelled` | — |
| Enum(str) | `EndReason`（`run.py:22`） | 运行结束原因（8 种）：`final_answer`/`step_limit`/`budget_exceeded`/`timeout`/`cancelled`/`error`/`human_handoff`/`knowledge_no_hit`（检索全 miss 短路拒答，Run 仍 COMPLETED、未调模型，`run.py:30-31`） | — |
| Model | `TokenUsage`（`run.py:32`） | token 用量：`input_tokens`/`output_tokens`/`model_calls`，`total` 为计算属性 | — |
| Model | `Run`（`run.py:42`） | 一次运行的完整记录；`error` 存 `LubanError.to_dict()["error"]` | — |
| Model(Generic) | `RunContext[DepsT]`（`run.py:67`） | 运行时传给工具/能力的上下文，`deps` 由调用方注入；含 `session_id`/`user_id`（透传观测标签，子 Run 继承） | — |
| Enum(str) | `TraceEventType`（`trace.py:12`） | Trace 事件类型（19 种，覆盖 run/step/model/tool/budget/fallback/knowledge 守门生命周期） | — |
| Model | `TraceEvent`（`trace.py:32`） | 单条 Trace 事件：`id`/`run_id`/`type`/`step_index`/`payload`/`created_at` | — |
| Model | `Step`（`trace.py:41`） | 一次执行步骤（模型调用/工具调用/图节点）：`kind: Literal["model","tool","graph_node"]` | — |
| Model | `Trace`（`trace.py:53`） | Trace 头：`id`/`run_id`/`status`/`started_at`/`ended_at` | — |
| Enum(str) | `RiskLevel`（`tool.py:9`） | 工具风险等级：`low`/`medium`/`high`，高风险默认要求审批 | — |
| Model | `ToolContract`（`tool.py:17`） | 工具执行契约：`timeout_seconds`/`max_retries`/`idempotent`/`approval_required`，由代码强制执行而非 Prompt 声明 | — |
| Model | `ToolDescriptor`（`tool.py:26`） | 工具注册描述：`key`/`name`/`description`/`args_schema`/`risk`/`contract`/`signature_hash` | — |
| Model | `SkillMeta`（`skill.py:6`） | L1 元数据：`name`/`description`/`version`，扫描常驻注入 system prompt | — |
| Model | `SkillPackage`（`skill.py:14`） | L1+L2 定位信息：`meta`/`path`/`scripts`/`references`/`assets`/`content_hash` | — |
| Enum(str) | `ErrorCode`（`errors.py:7`） | 统一错误码（24 种，覆盖 Agent/Prompt/Model/Tool/Run/Auth/Config/Knowledge 各域；`KNOWLEDGE_SOURCE_NOT_FOUND` → 404 为知识库管理新增） | — |
| Exception | `LubanError`（`errors.py:31`） | 框架统一异常：`code`/`message`/`details`/`retryable`，`to_dict()` 可整体序列化存储 | 构造时不校验，业务代码按需 raise |
| Exception | `CapabilityMismatchError`/`BudgetExceededError`/`StepLimitExceededError`/`RunTimeoutError`/`RunCancelledError`/`ToolApprovalRequiredError`（`errors.py:62-90`） | `LubanError` 的语义化子类，各自固定对应 `ErrorCode` | 同 `LubanError` |
| 函数 | `is_secret_key(key: str) -> bool`（`redact.py:68-74`） | 字段级秘密键判定：键名归一化（小写去符号，`normalized_key`，`redact.py:64-65`）后命中内置秘密键集合（`redact.py:17-41`，含 `apikey`/`authorization`/`token`/`password`/`secret`/`credential` 等）、部署者经 `register_secret_keys` 注册的额外键、或 `*apikey`/`*token`/`*password`/`*secret`/`*credential` 等后缀（`redact.py:43-51`）即真；与 H06 模型 `params` 校验同源 | 无 |
| 函数 | `redact_value(value: Any) -> Any`（`redact.py:88-103`） | 构造脱敏展示副本（不改源数据）：dict/list/tuple 递归；秘密键 → `[REDACTED]`；含 userinfo 或密钥形 query 的 URL 字符串（`_unsafe_url`，`redact.py:77-85`）、含 `bearer ` 的字符串 → `[REDACTED]`；供 Trace 落库/SSE 外发/观测后端/错误 details 各审计边界复用 | 无 |
| 函数 | `register_secret_keys(*keys: str) -> None`（`redact.py:56-61`） | 部署者注册业务特有的额外敏感键名（归一化后与内置规则同权命中，进程级生效） | 无 |

#### `lubanagent/extension`（Protocol + Registry + 装饰器）

| 类型 | 名称 | 说明 | 错误情况 |
|---|---|---|---|
| Model | `GraphDescriptor`（`protocols.py:12`） | 图注册描述：`name`/`description`/`builder`（返回 LangGraph 编译图的可调用对象），extension 本身不 import langgraph | — |
| Model | `Principal`（`protocols.py:20`） | 框架中立鉴权主体：`subject`/`scopes`/`claims` | — |
| Model | `AuthRequest`（`protocols.py:28`） | 框架中立鉴权请求：`headers`/`query_params`，api 层负责从 FastAPI `Request` 适配 | — |
| Protocol(runtime_checkable) | `ToolProvider`（`protocols.py:36`） | `tools() -> list[ToolDescriptor]` | 用户未实现对应方法则 `isinstance` 检查失败 |
| Protocol(runtime_checkable) | `GraphProvider`（`protocols.py:41`） | `graphs() -> list[GraphDescriptor]` | 同上 |
| Protocol(runtime_checkable) | `AuthProvider`（`protocols.py:46`） | `async verify(request: AuthRequest) -> Principal`；失败抛 `LubanError(ErrorCode.AUTH_FAILED)`（约定，非强制校验） | 校验失败应抛 `LubanError` |
| Protocol(runtime_checkable) | `TraceHandle`（`protocols.py:52`） | 观测后端不透明句柄（空 Protocol，仅作类型标记） | — |
| Protocol(runtime_checkable) | `ObservabilityBackend`（`protocols.py:57`） | `name`（属性）/`start_trace(run) -> TraceHandle`/`emit(handle, event)`/`end_trace(handle, status, summary=None)` | — |
| Protocol(runtime_checkable) | `Hook`（`protocols.py:68`） | 全 async 运行期钩子：`pre_run(ctx)`/`pre_tool(ctx, tool, args)`/`post_step(ctx, step)` | — |
| Model | `Snippet`（`protocols.py:78`） | 知识检索片段：`content`/`source`/`score`/`metadata` | — |
| Protocol(runtime_checkable) | `KnowledgeConnector`（`protocols.py:88`） | `async retrieve(query, top_k) -> list[Snippet]` | — |
| Model | `SessionState`（`protocols.py:93`） | 渠道层会话状态机快照：`session_key`/`state`/`data`/`updated_at`，`state` 取值由渠道层定义 | — |
| Protocol(runtime_checkable) | `SessionStateStore`（`protocols.py:103`） | 渠道层扩展点：`async get(session_key) -> SessionState \| None`/`async save(state)` | — |
| Model | `GuardVerdict`（`protocols.py:108`） | 出站风控判定：`allowed`/`reason`/`sanitized_text`（可选改写后安全文本） | — |
| Protocol(runtime_checkable) | `OutputGuard`（`protocols.py:117`） | 渠道层扩展点：`async check(text, *, context) -> GuardVerdict` | — |
| Protocol(runtime_checkable) | `ChannelConnector`（`protocols.py:122`） | 渠道层扩展点：`name`（属性）/`async start()`/`async stop()` | — |
| Class | `Registry`（`registry.py:24`） | 用户扩展运行期注册表，方法均为单数形式；见下方明细 | — |
| dataclass(frozen) | `RegisteredTool`（`registry.py:18`） | `descriptor: ToolDescriptor` + `handler: Callable` 的组合 | — |
| 函数 | `tool(*, key=None, description="", risk=RiskLevel.LOW, timeout_seconds=30.0, max_retries=0, idempotent=False, approval_required=False, registry=None) -> Callable`（`decorators.py:37`） | 装饰函数生成 `ToolDescriptor` 并注册，返回原函数（保持可直接调用） | 重复 `key` 注册抛 `LubanError(ErrorCode.CONFIG_ERROR)`（经 `Registry.register_tool`） |
| 函数 | `graph(*, name=None, description="", registry=None) -> Callable`（`decorators.py:72`） | 装饰图构建函数生成 `GraphDescriptor` 并注册，返回原函数 | 重复 `name` 注册抛 `LubanError(ErrorCode.CONFIG_ERROR)` |
| 函数 | `hook(*, registry=None) -> Callable`（`decorators.py:89`） | 装饰 `Hook` 实现类，实例化并追加注册，返回原类 | 无重复校验（`hooks` 是 list，允许多个） |
| 函数 | `discover(packages: Iterable[str], *, registry=None) -> int`（`decorators.py:111`） | 动态导入每个包及其全部子模块以触发装饰器注册，返回本次新注册条目总数；已导入模块跳过（`sys.modules` 缓存），重复调用幂等；`discover()` 本身不含 try/except | 包不存在时 `importlib.import_module` 抛 `ModuleNotFoundError`，`discover()` 不捕获，由唯一调用方 `lubanagent/app/factory.py:44-50` 按 `exc.name == package` 判断是否吞掉（详见"行为·调用树"） |
| 全局单例 | `default_registry`（`registry.py:118`） | 进程级默认 `Registry` 实例 | — |

`Registry` 方法明细（`registry.py`，均为单数形式）：

| 方法 | 签名 | 说明 | 错误情况 |
|---|---|---|---|
| `register_tool` | `(descriptor: ToolDescriptor, handler: Callable) -> None`（L37） | 注册工具 | key 已存在抛 `LubanError(ErrorCode.CONFIG_ERROR)` |
| `get_tool` | `(key: str) -> RegisteredTool`（L42） | 按 key 取工具 | 未注册抛 `LubanError(ErrorCode.TOOL_NOT_FOUND)` |
| `has_tool` / `list_tools` | （L48/L51） | 判断存在 / 列出全部 | — |
| `register_graph` | `(descriptor: GraphDescriptor) -> None`（L55） | 注册图 | name 已存在抛 `LubanError(ErrorCode.CONFIG_ERROR)` |
| `get_graph` | `(name: str) -> GraphDescriptor`（L60） | 按 name 取图 | 未注册抛 `LubanError(ErrorCode.VALIDATION_ERROR)` |
| `list_graphs` | （L66） | 列出全部 | — |
| `register_auth` / `get_auth` | `(provider: AuthProvider) -> None` / `() -> AuthProvider \| None`（L70/L73） | 单例式注册；不注册为 `None`，框架默认走 API Key | — |
| `register_observability` / `get_observability` | 同上模式（L77/L80） | 单例式注册；不注册为 `None`，由 settings 选择 | — |
| `register_observability_backend` / `get_observability_backend` / `list_observability_backends` | `(name, backend)` / `(name) -> Backend \| None` / `() -> list[str]`（L84/L89/L92） | 多命名后端注册，用户注册优先于内置 | name 重复抛 `LubanError(ErrorCode.CONFIG_ERROR)` |
| `register_hook` / `hooks` | `(hook: Hook) -> None` / `() -> list[Hook]`（L96/L99） | 追加式注册（list），无重复校验 | — |
| `register_knowledge` / `get_knowledge` / `list_knowledge` / `unregister_knowledge` | `(name, connector)` / `(name) -> KnowledgeConnector` / `() -> list[str]` / `(name) -> None`（L103/L108/L113/L117） | 命名知识源注册；`unregister_knowledge` 幂等（未注册名静默跳过），删除托管知识源时配套调用 | name 重复抛 `CONFIG_ERROR`；未注册取值抛 `VALIDATION_ERROR` |

#### `lubanagent/config`（配置加载）

| 类型 | 名称 | 说明 | 错误情况 |
|---|---|---|---|
| Class(BaseSettings) | `Settings`（`settings.py:13`） | pydantic-settings 模型，字段见下方"数据模型"章节 | 字段类型校验失败抛 pydantic `ValidationError` |
| classmethod | `Settings.load() -> Settings`（`settings.py:61`） | 分层入口：读 `os.environ["LUBAN_ENV"]`（缺省 `dev`），按 `(".env", ".env.{env}")` 顺序加载 | — |
| property | `Settings.sync_database_url`（`settings.py:54`） | Alembic 用同步驱动 URL：`+aiosqlite→+pysqlite`、`+asyncmy→+pymysql`，其他方言原样返回 | — |
| 函数(`@lru_cache`) | `get_settings() -> Settings`（`settings.py:71`） | 进程级单例配置入口，内部调用 `Settings.load()` | — |
| 函数 | `configure_logging(level: str = "INFO", fmt: Literal["console","json"] = "console") -> None`（`logging.py:30`） | 配置 root logger；重复调用先清已有 handler 不叠加 | — |
| Class(logging.Formatter) | `JsonFormatter`（`logging.py:12`） | 单行 JSON 日志格式，`LogRecord` 非标准属性并入 JSON | — |
| 常量 | `DEFAULT_SQLITE_URL`（`defaults.py:3`） | `"sqlite+aiosqlite:///./lubanagent.db"` | — |
| 常量 | `USER_EXTENSION_PACKAGES`（`defaults.py:6`） | `("app.tools", "app.graphs")`，约定优于配置的用户扩展扫描入口 | — |
| 常量 | `FAKE_MODEL_KEY`（`defaults.py:8`） | `"fake-chat"`，零密钥兜底模型 key | — |
| 常量 | `SEED_MODEL_DEFINITIONS`（`defaults.py:12`） | 零外部密钥环境全绿依据；形状 = `ModelDefinition.model_dump(mode="json")`，可直接 `model_validate` 消费 | — |
| 常量 | `SEED_BRAIN_MAPPINGS`（`defaults.py:35`） | `{"fast"/"smart"/"cheap"/"vision": FAKE_MODEL_KEY}` 四槽位种子映射 | — |

## 数据模型

本模块（`core`）是 pydantic dataclass 领域模型，**无对应数据库表**；持久化落地见 `persistence` 模块的 ORM 映射（例如 `AgentDefinition`/`AgentVersion` 对应 `persistence/models/agent.py` 的 `AgentRow`/`AgentVersionRow`，`persistence/models/agent.py:14-32`）。`extension`/`config` 无数据模型（纯运行期对象/环境变量）。以下按关键 dataclass 列出字段结构（字段名/类型/约束，来源见"接口契约"表内行号，此处不重复列出全部，仅列结构较复杂或跨模块引用频繁者）：

### `AgentDefinition`（`lubanagent/core/definition.py:57`）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| key | str | 必填 | Agent 唯一键 |
| name | str | 必填 | 展示名 |
| description/role/greeting/instructions | str | 默认 `""` | 文案类字段；`instructions` 始终是权威落地内容（引用 Prompt 库时由落库前解析写入） |
| avatar | str \| None | 默认 `None` | 虚拟形象落盘文件名（`avatar.svg/png/glb/gltf`，存 `avatar_files_root/{key}/`）；纯展示字段，运行期逻辑零感知；经 `PUT /v1/agents/{key}/avatar` 上传时由服务端设置（上传即版本） |
| prompt_variables | dict[str, str] | 默认 `{}` | Prompt 变量表；引用 Prompt 解析时与 Prompt 变量合并，Agent 自带同名变量覆盖 |
| prompt_key | str \| None | 默认 `None` | Prompt 库引用（双轨）：非空时落库前服务端解析 prompt 内容写入 `instructions`；`None` = 纯内嵌（存量路径零行为变化） |
| prompt_version | int \| None | 默认 `None` | 解析时钉住的 Prompt 版本（溯源）；非空按钉住版解析，`null` 取当前版 |
| brain | `BrainConfig` | 默认 `BrainConfig()` | 大脑槽位配置 |
| tools/mcp_servers/skills | list[str] | 默认 `[]` | 引用的能力 key 列表 |
| knowledge | `KnowledgeConfig` | 默认 `KnowledgeConfig()` | 知识检索配置 |
| memory | `MemoryConfig` | 默认 `MemoryConfig()` | 记忆配置 |
| boundary | `BoundaryConfig` | 默认 `BoundaryConfig()` | 运行边界 |
| io | `IOContract` | 默认 `IOContract()` | 输入输出契约 |
| tool_signatures | dict[str, str] | 默认 `{}` | 用于版本冻结校验（发布时快照工具签名） |
| version | int | 默认 `1` | 版本号 |
| status | `AgentStatus` | 默认 `DRAFT` | 生命周期状态 |

### `Run`（`lubanagent/core/run.py:42`）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | str | 必填 | 运行 ID |
| agent_key/agent_version | str/int | 必填 | 定位到具体冻结版本 |
| session_id/user_id/parent_run_id | str \| None | 默认 `None` | `parent_run_id` 支撑编排 Run 为会话 Run 子 Run；`user_id` 为透传观测标签（无用户管理语义），随 Run 落库供按用户排查，子 Run 继承（`run.py:48-50`） |
| status | `RunStatus` | 默认 `PENDING` | 状态机当前值 |
| input | dict[str, Any] | 默认 `{}` | 输入负载 |
| output | str \| None | 默认 `None` | 最终输出 |
| brain_profile | `BrainProfile` | 必填 | 本次运行实际使用槽位 |
| model_key | str \| None | 默认 `None` | 大脑映射解析出的模型 key |
| end_reason | `EndReason` \| None | 默认 `None` | 结束原因 |
| token_usage | `TokenUsage` | 默认 `TokenUsage()` | 用量汇总 |
| cost | float | 默认 `0.0` | 费用 |
| error | dict[str, Any] \| None | 默认 `None` | 存 `LubanError.to_dict()["error"]` |
| created_at/started_at/ended_at | datetime/datetime\|None | `created_at` 必填 | 时间戳 |

### `Settings`（`lubanagent/config/settings.py:13`，env 变量名见注释）

| 字段 | 类型 | 默认值 | env 变量 |
|---|---|---|---|
| env | `Literal["dev","staging","prod"]` | `"dev"` | `LUBAN_ENV` |
| debug | bool | `False` | `DEBUG` |
| log_level | str | `"INFO"` | `LOG_LEVEL` |
| log_format | `Literal["console","json"]` | `"console"` | `LOG_FORMAT` |
| database_url | str | `DEFAULT_SQLITE_URL` | `DATABASE_URL` |
| observability_backend | str | `"langfuse"` | `OBSERVABILITY_BACKEND` |
| langfuse_host/langfuse_public_key/langfuse_secret_key | str \| None | `None` | `LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` |
| langsmith_api_key/langsmith_project | str \| None | `None` | `LANGSMITH_API_KEY`/`LANGSMITH_PROJECT` |
| auth_enabled | bool | `True` | `AUTH_ENABLED` |
| api_key | str \| None | `None` | `API_KEY` |
| rate_limit_per_minute | int | `600` | `RATE_LIMIT_PER_MINUTE` |
| biz_base_url | str \| None | `None` | `BIZ_BASE_URL` |
| embedding_model | str \| None | `None` | `EMBEDDING_MODEL` |
| embedding_base_url | str \| None | `None` | `EMBEDDING_BASE_URL` |
| embedding_api_key_env | str \| None | `None` | `EMBEDDING_API_KEY_ENV` |

Embedding 三字段语义（`settings.py:39-43` 注释）：配置了 `embedding_model` 时知识源 embedder 走 OpenAI 兼容协议（`OpenAICompatibleEmbedding`），未配置则由装配方（`lubanagent/app/factory.py:82-90`）退回 `FakeEmbedding`（零密钥演示，不承诺生产检索质量）。
| extension_packages | list[str]（`NoDecode`） | `list(USER_EXTENSION_PACKAGES)` | `EXTENSION_PACKAGES`（逗号分隔，`field_validator` 拆分） |
| skills_root | str | `"app/skills"` | `SKILLS_ROOT` |
| knowledge_files_root | str | `"data/knowledge"` | `KNOWLEDGE_FILES_ROOT`（工作台托管知识源文件落盘根目录，每源一子目录，`settings.py:51`） |
| long_term_memory_capacity | int | `1000` | `LONG_TERM_MEMORY_CAPACITY`（长期记忆每 agent+user 条数上限，超出淘汰最旧，`settings.py:53`；由 `LongTermMemory` 构造期读取，`memory/long_term.py:18-22`） |
| avatar_files_root | str | `"data/avatars"` | `AVATAR_FILES_ROOT`（Agent 虚拟形象文件落盘根目录，每 Agent 一子目录，`settings.py:52`） |

索引：无（非数据库表，此处不适用）。

## 行为

### 状态机

无状态机（`AgentStatus`/`RunStatus`/`TraceEventType` 等枚举本身是状态取值定义，状态**流转**逻辑在上层模块——`AgentStatus` 流转见 `persistence`/`runtime` 相关模块，`RunStatus` 流转见 `runtime` 模块，本模块只定义取值集合）。

### 调用树

无经典意义的"应用服务调用树"（本模块不含业务服务）。以 Registry 装饰器自动发现流程代替，描述框架启动时用户扩展如何被发现并挂入：

```
create_app()  # lubanagent/app/factory.py:27
├── get_settings()  # 读取 settings.extension_packages（默认 ("app.tools","app.graphs")）
└── for package in settings.extension_packages:  # lubanagent/app/factory.py:44-50
    └── try: discover([package], registry=registry)  # lubanagent/extension/decorators.py:111，容错分支属调用方 factory.py，非 discover() 内部
        │   ├── importlib.import_module(package)  # 触发包 __init__ 执行
        │   ├── pkgutil.walk_packages(...)  # 递归遍历子模块
        │   │   └── importlib.import_module(每个未加载子模块)  # sys.modules 已存在则跳过, 幂等（decorators.py:124-126）
        │   └── 模块被导入的过程中, 模块顶层的 @tool/@graph/@hook 装饰器语句被执行
        │       ├── @tool(...)  # decorators.py:37 → Registry.register_tool(descriptor, fn)
        │       ├── @graph(...)  # decorators.py:72 → Registry.register_graph(descriptor)
        │       └── @hook(...)  # decorators.py:89 → Registry.register_hook(cls())
        └── except ModuleNotFoundError as exc:  # factory.py:47-50，discover() 本身不捕获任何异常
            ├── [exc.name != package]  # 包内部 import 失败 → raise（不吞）
            └── [exc.name == package]  # 扩展包本身不存在 → 记 warning 跳过（空脚手架也能起）
```

补充：`register_auth`/`register_observability`/`register_observability_backend`/`register_knowledge` 不由装饰器自动触发，需在 `app/main.py` 等用户装配代码中显式调用（`docs/design-docs/LubanAgent-architecture-design.md:357` 佐证：仅 Skill 包、知识源灌入、自定义鉴权/观测后端等场景需手动注册）。

配置加载调用序列（`lubanagent/app/factory.py:35-37`）：

```
create_app()
├── settings = get_settings()  # lubanagent/config/settings.py:71, @lru_cache 进程级单例
│   └── Settings.load()  # settings.py:61
│       ├── env = os.environ.get("LUBAN_ENV", "dev")
│       └── Settings(_env_file=(".env", f".env.{env}"))  # 真实环境变量 > .env.{env} > .env
└── configure_logging(settings.log_level, settings.log_format)  # config/logging.py:30
    ├── 清空 root logger 已有 handler（重复调用不叠加）
    └── 按 fmt 选择 JsonFormatter 或纯文本 Formatter
```

## 边界规则

- **依赖方向铁律（领地隔离）**：`lubanagent/* → app/*` 禁止。框架运行期只能经 `Registry` 取用户注册的对象，不得 `import app.xxx`（来源：`importlinter.toml:7-11` CI 契约 "领地隔离：lubanagent → app 禁止"；代码实测 `lubanagent` 目录下无任何 `from app.` / `import app.` 语句）。
- **分层依赖铁律（core/extension/config 内部）**：`core` 是最底层，禁止依赖 `extension`/`config`/`persistence` 等任何兄弟包及 `sqlalchemy`/`fastapi`/`langchain` 等重依赖（`importlinter.toml:25-36`）；`extension` 只能依赖 `core`，禁止依赖 `config`/`persistence`/`model` 等上层包（`importlinter.toml:38-49`）；`config` 只能依赖 `core`，禁止依赖 `extension`/`persistence`/`model` 等上层包（`importlinter.toml:51-62`）。三者互不形成环依赖。
- **商业边界**：`lubanagent → lubanagent_channels` 禁止导入（`importlinter.toml:13-17`），渠道层三扩展点（`ChannelConnector`/`SessionStateStore`/`OutputGuard`）只在 `extension/protocols.py` 定义接口，不含任何实现，实现留给商用渠道层模块。
- **Protocol 契约而非继承**：所有扩展点均为 `@runtime_checkable` 的 `Protocol`（`extension/protocols.py`），用户扩展类无需继承任何基类，只需实现对应方法签名（如 `ToolProvider` 只需实现 `tools() -> list[ToolDescriptor]`）即满足契约，可用 `isinstance(obj, ToolProvider)` 运行期校验。
- **Registry 重复注册即报错**：`register_tool`/`register_graph`/`register_observability_backend`/`register_knowledge` 对同一 key/name 重复注册均抛 `LubanError(ErrorCode.CONFIG_ERROR)`（`registry.py:39/57/86/105`），防止用户扩展包之间静默覆盖。`register_auth`/`register_observability`/`register_hook` 无重复校验：前两者是单例覆盖式注册（后注册覆盖前者），`register_hook` 是追加式（允许多个 Hook 共存）。
- **`AgentVersion` 发布即冻结**：`model_config = ConfigDict(frozen=True)`（`definition.py:87`），版本快照创建后字段不可变，防止已发布版本被运行期篡改（对应"Agent 定义层"契约：库内版本化、发布即冻结）。`PromptVersion` 同型同规（`prompt.py:22`）。
- **`discover()` 幂等，容错在调用方**：`discover()` 自身（`decorators.py:111-127`）已导入模块通过 `sys.modules` 判断跳过（`decorators.py:124-126`，该行是 `pkgutil.walk_packages` 子模块导入循环，非容错分支），重复调用不会重复注册；`discover()` 本身**不含** try/except，不捕获任何异常。容错处理在唯一调用方 `lubanagent/app/factory.py:44-50`：用 `try/except ModuleNotFoundError` 包裹每个包的 `discover()` 调用，扩展包本身不存在（`exc.name == package`）只记 warning 并继续，保证"空脚手架也能起"；但包内部导入失败（`exc.name != package`）会原样 `raise`，不会被吞掉。
- **配置优先级铁律**：真实环境变量 > `.env.{env}` > `.env`（`settings.py:63-68` docstring 明确声明），`get_settings()` 用 `@lru_cache` 保证进程内单例，测试如需重新加载需显式清缓存：直接调用标准库 `lru_cache` 自带的 `get_settings.cache_clear()`（本模块未额外封装便捷方法，调用证据见 `tests/lubanagent/config/test_settings.py:111,115`，测试用例在断言前后各调一次以隔离缓存状态）。
- **不存密钥本体**：`ModelDefinition.api_key_env` 只存环境变量名，不存密钥值本身（`model.py:22` docstring "模型定义: 模型注册键、能力清单与固定使用参数, 不存密钥本体"）。
- **脱敏是字段级键名规则 + 凭证形态字符串识别，不做任意业务文本 PII 值级检测**：`redact_value` 只命中秘密键命名字段、含 userinfo/密钥 query 的 URL、含 `bearer ` 的字符串三类形态（`redact.py:88-103` 模块 docstring 明示策略边界）；异常 traceback 仅进日志，是否落盘由部署方日志策略管控，本组件不处理；`redact_value` 只读源数据返回新结构，执行路径必须用真实参数，绝不用副本执行（测试：`tests/lubanagent/core/test_redact.py`）。

## 名词

| 术语 | 语义 |
|---|---|
| BrainProfile | 逻辑大脑槽位，取值 `fast`/`smart`/`cheap`/`vision` 四档，用于解耦 Agent 定义与具体模型；切换模型只需改库内 `brain_profile → model_key` 映射，不改代码不发版（`core/brain.py`） |
| Prompt 库 / 双轨引用 | Prompt 是独立版本化的一等实体（`PromptDefinition`/`PromptVersion`）；Agent 定义经 `prompt_key`/`prompt_version` 双轨引用——非空时落库前解析 prompt 内容写入 `instructions` 并落进 Agent 版本快照，为空走纯内嵌存量路径；解析入口 `resolve_prompt_reference` 在 persistence 模块（`core/prompt.py`、`core/definition.py:69-70`） |
| Registry | 用户扩展的运行期唯一注册表；框架不 import 用户模块，只在运行时查此表取工具/图/鉴权/观测/知识源/钩子等扩展实例（`extension/registry.py`） |
| discover | 动态包扫描函数，导入指定包及其全部子模块以触发模块顶层的 `@tool`/`@graph`/`@hook` 装饰器执行，从而完成"装饰即注册"（`extension/decorators.py:111`） |
| ToolDescriptor | 工具注册描述，包含 `signature_hash`（函数签名摘要），用于工具版本兼容性治理与 Agent 版本冻结校验（`core/tool.py:26`） |
| ToolContract | 工具执行契约，`timeout_seconds`/`max_retries`/`idempotent`/`approval_required` 由代码强制执行，体现"确定性规则由代码执行，不靠 Prompt 声明"（`core/tool.py:17`） |
| signature_hash / tool_signatures | 前者是单个工具的函数签名 sha256 摘要（`decorators.py:33` `_signature_hash`）；后者是 `AgentDefinition` 上记录的工具签名快照表，发布时比对以判定版本是否需要重新冻结（`core/definition.py:75`） |
| RunContext[DepsT] | 运行时传给工具/能力的类型化上下文，`deps` 字段由调用方注入自定义依赖对象（依赖注入点），是 LangGraph 节点/工具函数访问预算、用量、元数据的统一入口；`user_id` 字段为透传观测标签（无用户管理语义），图/多 Agent 子 Run 经 `parent_ctx.user_id` 继承（`core/run.py:67,75`） |
| LubanError / ErrorCode | 框架统一异常与错误码体系；`LubanError.to_dict()` 结构直接写入 `Run.error`/`Trace` 存储，前后端共享同一错误码表（`core/errors.py`） |
| Trace 七问 | `trace.py` 模块 docstring 术语，指 Trace 事件流需回答的运行可观测性核心问题；具体清单（Agent 版本/模型/Prompt/工具调用/Token/费用/结束原因）定义在 `observability-trace-cost` 模块文档，详见 `docs/specs/modules/observability-trace-cost.md` 名词章节「七问」词条 |
| USER_EXTENSION_PACKAGES | 约定优于配置的用户扩展扫描入口默认值 `("app.tools", "app.graphs")`，框架 import 期不导入，仅运行期由 `extension.discover` 动态导入（`config/defaults.py:6`） |
| SEED_MODEL_DEFINITIONS / SEED_BRAIN_MAPPINGS | 零外部密钥环境下的种子模型定义与四槽位种子映射，支撑"零配置克隆即可跑通"（`config/defaults.py:12/35`） |
| redact_value | 共享脱敏组件的核心函数：对 dict/list/tuple 构造脱敏展示副本（秘密键/凭证形态 URL/bearer 字符串 → `[REDACTED]`），不改源数据；Trace 落库、SSE 外发、观测后端外发、Run 错误 details 四条审计边界共用同一实现（`core/redact.py:88-103`） |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| 操作系统环境变量 / `.env` 文件 | `Settings.load()`（`config/settings.py:61`） | 读取 `LUBAN_ENV`、`DATABASE_URL`、`API_KEY` 等全部配置项，优先级：真实环境变量 > `.env.{env}` > `.env` |
| Python 标准库 `importlib`/`pkgutil`/`sys` | `discover()`（`extension/decorators.py:111`） | 动态导入用户扩展包及其子模块，依赖 `sys.modules` 缓存判定是否已导入 |
| pydantic / pydantic-settings（第三方库） | 全部 `BaseModel`/`BaseSettings` 子类 | 数据校验、序列化（`model_dump`）、环境变量绑定 |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent/app/factory.py`（应用装配根） | `create_app()` 内 `get_settings()`/`configure_logging()`/`discover()`（`app/factory.py:36-50`） | 装配阶段依次读取配置、配置日志、扫描用户扩展包完成 Registry 填充 |
| `lubanagent/persistence`（持久化模块） | ORM 模型字段结构参照 `core` dataclass（如 `AgentRow`/`AgentVersionRow` 对应 `AgentDefinition`/`AgentVersion`（`persistence/models/agent.py:14-32`），`PromptRow`/`PromptVersionRow` 对应 `PromptDefinition`/`PromptVersion`（`persistence/models/prompt.py:13-40`）） | `core` 的 pydantic 模型是持久化层字段设计与序列化/反序列化的权威来源 |
| `lubanagent/runtime`/`lubanagent/tool`/`lubanagent/model`/`lubanagent/mcp`/`lubanagent/graph` 等上层能力模块 | 均 `from lubanagent.core import ...` / `from lubanagent.extension import ...` / `from lubanagent.config import ...` | 复用 `Run`/`RunContext`/`ToolDescriptor`/`BrainProfile`/`LubanError`/`Registry`/`Settings` 等作为跨模块统一契约类型，避免各模块各自定义重复类型 |
| `app/*`（用户领地代码） | `@tool`/`@graph`/`@hook` 装饰器、`Registry` 各 `register_*` 方法、`ToolProvider` 等 Protocol | 用户扩展代码通过这些装饰器/接口挂入框架运行期，是 `lubanagent/* → app/*` 禁止导入前提下用户扩展生效的**唯一**合法路径 |
| `lubanagent-channels`（商用渠道层，独立仓库/包，不在本仓库） | `ChannelConnector`/`SessionStateStore`/`OutputGuard` 三个 Protocol（`extension/protocols.py:93-127`） | 渠道层实现这三个 Protocol 以接入框架；核心不依赖渠道层实现，卸载渠道层核心仍可通过 REST/SSE 独立运行 |
