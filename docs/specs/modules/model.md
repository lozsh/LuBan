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

# model 模块

## 定位

`model` 模块是 LubanAgent 的模型工厂与逻辑大脑解析层，管理"模型注册记录（`ModelDefinition`）→ 可执行 `BaseChatModel`"的构造，以及"逻辑大脑槽位（`BrainProfile`）→ 具体模型"的运行期解析与能力校验。核心不是数据库实体的 CRUD 模块，而是纯计算/纯适配层：无数据库依赖（不 import `persistence`/`sqlalchemy`），持久化结构定义在 `lubanagent/persistence/models/model_center.py`（详见"数据模型"节）。模块位置：`lubanagent/model/`（`factory.py`、`openai_compat.py`、`brain_resolver.py`、`capability.py`、`fakes.py`）。

分层依据（来源：`importlinter.toml:77-88`）：`model` 只允许依赖 `lubanagent.core` + `langchain_core`/`langchain_openai` + pydantic + 标准库；禁止依赖 `persistence`/`runtime`/`api`/`app` 等一切上层与具体存储实现，通过 `BrainMappingStore` Protocol 解耦持久化。

## 接口契约

### Python API

本模块是框架能力层，非 HTTP 服务，对外暴露的是 Python 类/函数签名（均从 `lubanagent/model/__init__.py` re-export，见 `lubanagent/model/__init__.py:24-44`）。

| 类别 | 签名 | 说明 | 错误情况 | 来源 |
|---|---|---|---|---|
| 工厂类 | `class ModelFactory` | 按 `definition.factory` 名分发到具体构造函数的注册表 | — | `lubanagent/model/factory.py:12-38` |
| 工厂方法 | `ModelFactory.register(self, name: str, fn: ChatModelFactoryFn) -> None` | 注册命名工厂函数 | 重名注册 → `LubanError(ErrorCode.CONFIG_ERROR)` | `lubanagent/model/factory.py:18-25` |
| 工厂方法 | `ModelFactory.has(self, name: str) -> bool` | 判断工厂名是否已注册 | — | `lubanagent/model/factory.py:27-28` |
| 工厂方法 | `ModelFactory.create(self, definition: ModelDefinition) -> BaseChatModel` | 按 `definition.factory` 分发构造 | 未知 factory 名 → `LubanError(ErrorCode.CONFIG_ERROR, details={"factory": ...})` | `lubanagent/model/factory.py:30-38` |
| 模块级单例 | `default_model_factory = ModelFactory()` | 预注册 `"openai_compat"`、`"fake"` 两个工厂（在 `__init__.py` 尾部注册） | — | `lubanagent/model/factory.py:41`；注册见 `lubanagent/model/__init__.py:20-22` |
| 便捷函数 | `create_chat_model(definition: ModelDefinition) -> BaseChatModel` | 走 `default_model_factory.create()` | 同 `ModelFactory.create` | `lubanagent/model/factory.py:44-46` |
| 类型别名 | `ChatModelFactoryFn = Callable[[ModelDefinition], BaseChatModel]` | 工厂函数签名 | — | `lubanagent/model/factory.py:9` |
| OpenAI 兼容工厂 | `create_openai_compatible(definition: ModelDefinition) -> BaseChatModel` | 构造 `ChatOpenAI`；**先调 `validate_model_connection`（读取密钥环境变量之前）校验连接边界，再构造**，密钥只来自 `definition.api_key_env` 指向的环境变量；`base_url` 缺省兜底 `PROVIDERS[provider]["base_url"]` | 连接边界校验失败（`ValueError`）→ `LubanError(ErrorCode.VALIDATION_ERROR)`，错误消息不含输入值；`api_key_env` 非 None 但环境变量未设置 → `LubanError(ErrorCode.CONFIG_ERROR, details={"api_key_env": ...})` | `lubanagent/model/openai_compat.py:24-58` |
| 便捷构造 | `openai_compat_definition(*, key, provider, model_name, capability=None, params=None, base_url=None) -> ModelDefinition` | 从 `PROVIDERS` 预填 `base_url`/`api_key_env`，可被显式参数覆盖 | `provider` 不在 `PROVIDERS` → `LubanError(ErrorCode.VALIDATION_ERROR, details={"provider": ...})` | `lubanagent/model/openai_compat.py:68-94` |
| 常量 | `PROVIDERS: dict[str, dict[str, str]]` | 内置 provider 预设表（见“名词”节） | — | `lubanagent/model/openai_compat.py:12-21` |
| Protocol | `class BrainMappingStore(Protocol)`：`async def get_brain_mapping(self, env: str, profile: BrainProfile) -> ModelDefinition \| None` | 映射存储抽象；`persistence.ModelRepository` 天然满足，测试可 Fake | — | `lubanagent/model/brain_resolver.py:15-19` |
| 数据类 | `@dataclass(frozen=True) class BrainResolution` | 字段：`profile`、`definition`、`used_fallback`、`resolved_profile`、`fallback_events: list[TraceEvent]` | — | `lubanagent/model/brain_resolver.py:22-30` |
| 解析器 | `class BrainResolver.__init__(self, store: BrainMappingStore, *, env: str = "dev")` | 持有 store 与环境名 | — | `lubanagent/model/brain_resolver.py:36-38` |
| 解析方法 | `async BrainResolver.resolve(self, profile: BrainProfile) -> ModelDefinition` | 每次调用都直接查 store，不缓存映射 | 无映射 → `LubanError(ErrorCode.BRAIN_NOT_MAPPED, details={"env":..., "profile":...})` | `lubanagent/model/brain_resolver.py:40-48` |
| 能力报告 | `@dataclass(frozen=True) class CapabilityReport`：`ok: bool`，`missing: list[str]` | — | — | `lubanagent/model/capability.py:21-24` |
| 能力推导 | `required_capabilities(agent: AgentDefinition) -> list[str]` | 从 Agent 定义推导所需能力（规则见"边界规则"节） | — | `lubanagent/model/capability.py:27-36` |
| 能力校验 | `check_capability(agent: AgentDefinition, model: ModelDefinition) -> CapabilityReport` | 逐项对照 `required_capabilities` 与 `model.capability` | — | `lubanagent/model/capability.py:39-43` |
| 受控降级 | `async resolve_brain_for_agent(agent: AgentDefinition, resolver: BrainResolver) -> BrainResolution` | 主 profile 解析/能力不满足时按 `fallback_profiles` 顺序找首个可用 | 全部不满足 → `CapabilityMismatchError(details={"missing":..., "tried":...})` | `lubanagent/model/capability.py:63-121` |
| 测试模型 | `class ScriptedFakeChatModel(BaseChatModel)` | 按 `script` 顺序回放确定性响应（文本/工具调用/异常三态） | script 耗尽后再调用 → `LubanError(ErrorCode.INTERNAL_ERROR, details={"reason": "script exhausted"})` | `lubanagent/model/fakes.py:20-142` |
| 录制包装 | `class RecordingChatModel(BaseChatModel)`：`wrapped: BaseChatModel`, `record_path: str` | 包装任意 `BaseChatModel`，调用结果追加写 JSONL | 底层异常时记录 `{"type": "error", ...}` 后重新抛出 | `lubanagent/model/fakes.py:145-205` |
| 重放函数 | `replay_script(path: str \| Path) -> list[Any]` | 读 JSONL 还原为 `script` 列表 | 未知记录类型 → `LubanError(ErrorCode.INTERNAL_ERROR, details={"record": ...})` | `lubanagent/model/fakes.py:208-228` |
| Fake 构造 | `fake_definition(*, key="fake-chat", script=None, capability=None) -> ModelDefinition` | `factory="fake"`、`provider="fake"`；`capability` 缺省时六项能力全 `True` | — | `lubanagent/model/fakes.py:231-254` |
| Fake 工厂函数 | `create_fake(definition: ModelDefinition) -> ScriptedFakeChatModel` | 从 `definition.params["script"]` 构造 | — | `lubanagent/model/fakes.py:257-262` |

### RPC / MQ / Cron

无。本模块不含 RPC/MQ/Cron 接口；HTTP 层面的模型注册/大脑映射管理接口（`GET/PUT /models`、`GET/PUT /brain-mappings`）属于 `lubanagent/api/routes/models.py`，不在本模块范围（本模块被其调用，见"上下游"节）。

## 数据模型

本模块本身不定义 ORM 表、不 import `sqlalchemy`（`importlinter.toml:81-88` 强制禁止）。运行期数据结构是 `lubanagent/core` 定义的 pydantic 值对象：

- `ModelCapability`（`lubanagent/core/model.py:120-130`）：`chat: bool = True`、`streaming: bool = True`、`tool_calling: bool = False`、`structured_output: bool = False`、`vision: bool = False`、`reasoning: bool = False`、`context_window: int = 8192`、`max_output: int = 4096`。
- `ModelDefinition`（`lubanagent/core/model.py:133-170`）：`key: str`、`provider: str`、`model_name: str`、`factory: str`、`capability: ModelCapability`、`params: dict[str, Any]`、`base_url: str | None`、`api_key_env: str | None`、`enabled: bool = True`。**不存密钥本体**，只存环境变量名 `api_key_env`。构造即校验：`api_key_env`/`base_url` 两个 `field_validator` + `model_validator(mode="after")` 调用 `validate_model_connection`（`lubanagent/core/model.py:148-170`），非法定义在构造期即被拒绝；`model_config` 设 `hide_input_in_errors=True`，校验错误不回显输入值。
- `BrainProfile`（`lubanagent/core/brain.py:6-12`，`str, Enum`）：`FAST`/`SMART`/`CHEAP`/`VISION` 四值。

同文件（`lubanagent/core/model.py`）提供模型写入/脱敏边界三函数（不属于 `lubanagent.model` 包导出面，调用方从 `lubanagent.core.model` 直接导入，消费方为 `openai_compat`/`ModelRepository`/`api.routes.models`）。秘密键/凭证 URL/bearer 字符串的识别规则（`is_secret_key`/`_unsafe_url`/`normalized_key`/`redact_value`）自 H07 起上移至共享脱敏组件 `lubanagent/core/redact.py`，`model.py` 经 `from lubanagent.core.redact import _REDACTED, _unsafe_url, is_secret_key, normalized_key` 复用（`core/model.py:9`），三个公开函数签名与行为不变：

- `validate_model_connection(*, factory, params, base_url, api_key_env) -> None`（`:83-96`）：模型入库/构造统一边界。校验顺序：`api_key_env` 必须匹配 `^[A-Za-z_][A-Za-z0-9_]*$` → `base_url` 禁止 userinfo/查询参数/片段及密钥形 query 键（`_unsafe_connection_url`，`:67-69`）→ `params` 键归一化（小写去符号）后命中连接键集合 `_CONNECTION_KEYS`（`model`/`api_key`/`base_url`/`default_headers`/`http_client`/`proxy` 等，`:11-35`）即拒绝 → `params` 值含秘密键（`is_secret_key`：`redact.py:17-51` 内置键集合与 `*_apikey` 等后缀）、嵌套凭证 URL 或 `[REDACTED]` 占位值即拒绝 → `factory="openai_compat"` 时 `params` 键仅允许白名单 `_OPENAI_PARAMS`（`:36-52`）。错误消息不含输入值或密钥。
- `redact_model_params(value)`（`:99-117`）：构造展示副本，秘密键/连接键 → `[REDACTED]`，可疑 URL/bearer 字符串整体隐藏；只用于展示，不得用副本执行。
- `redact_registered_model_params(factory, params)`（`:55-64`）：管理界面仅展示 openai_compat 已知推理/计价字段（`_OPENAI_PARAMS` 且非秘密键），其余键（含非 openai_compat 工厂全部字段）值替换 `[REDACTED]`。

### 持久化结构见 persistence 模块

`ModelDefinition`/大脑映射的库内持久化定义在 `lubanagent/persistence/models/model_center.py`（只读交叉引用，不在本模块文档维护权威定义）：

- 表 `models`（`ModelRow`，`lubanagent/persistence/models/model_center.py:13-31`）：`key`（唯一）、`provider`、`model_name`、`factory`、`capability`（JSON）、`params`（JSON）、`base_url`、`api_key_env`、`enabled`、`created_at`、`updated_at`。
- 表 `brain_mappings`（`BrainMappingRow`，`lubanagent/persistence/models/model_center.py:34-47`）：`env` + `profile` 联合唯一约束，`model_key` 外键指向 `models.key`。

读写入口是 `lubanagent/persistence/repositories/model.py` 的 `ModelRepository`（`upsert_model`/`get_model`/`list_models`/`set_brain_mapping`/`get_brain_mapping`），`ModelRepository` 结构上满足本模块的 `BrainMappingStore` Protocol（`lubanagent/model/brain_resolver.py:15-19`），但本模块不直接 import 它，由上层（`runtime`）在运行时注入。

索引：
- `models.key`（唯一）— 模型注册键查找与外键引用基础（`lubanagent/persistence/models/model_center.py:17`）。
- `brain_mappings(env, profile)`（唯一约束）— 保证同环境下每个大脑槽位只有一条当前映射（`lubanagent/persistence/models/model_center.py:36`）。

## 行为

### 状态机

无（本模块不含实体状态流转；`ModelDefinition.enabled` 是布尔开关而非状态机，由 `list_models(enabled_only=True)` 在查询侧过滤，过滤逻辑在 `persistence` 层）。

### 调用树

#### 大脑解析与受控降级（`resolve_brain_for_agent`）

由上层（`runtime.AgentLoop._resolve_brain`，见"上下游"节）在每次 Run 启动前调用，将 Agent 的逻辑大脑配置解析为具体可用模型。前置限制：`BrainResolver` 不缓存映射，每次都直接查 `store`（`lubanagent/model/brain_resolver.py:1` 模块 docstring："每次 resolve 都过 store"），因此改库映射后下一次调用立即生效，无需发版。副作用：若发生降级，产出 `TraceEvent(type=MODEL_FALLBACK)` 留痕（落库由调用方 `runtime` 负责）。

```
resolve_brain_for_agent(agent, resolver)
├── resolver.resolve(agent.brain.profile)  # 主槽位
│   ├── [异常] LubanError(BRAIN_NOT_MAPPED) → reason="brain not mapped: ..." → 进入 fallback 分支
│   └── [成功] check_capability(agent, definition)
│       ├── [ok] 直接返回 BrainResolution(used_fallback=False, resolved_profile=primary, fallback_events=[])
│       └── [not ok] reason="capability mismatch, missing: ..." → 进入 fallback 分支
└── for fallback in agent.brain.fallback_profiles:  # 按声明顺序逐个尝试
    ├── resolver.resolve(fallback)
    │   └── [BRAIN_NOT_MAPPED] continue 下一个
    ├── check_capability(agent, fallback_definition)
    │   ├── [ok] 返回 BrainResolution(used_fallback=True, resolved_profile=fallback,
    │   │         fallback_events=[_fallback_event(from=primary, to=fallback, model_key, reason)])
    │   └── [not ok] 记录 last_missing，继续下一个 fallback
    └── [全部尝试完仍不满足] raise CapabilityMismatchError(details={"missing": last_missing, "tried": [...]})
```

#### 模型构造（`create_chat_model` / `create_openai_compatible`）

由上层在解析出 `ModelDefinition` 后调用，构造出可直接 `.invoke()`/`.stream()` 的 LangChain `BaseChatModel`。前置限制：密钥只能来自环境变量（不接受入参传密钥），`definition.factory` 必须已在 `default_model_factory` 注册；连接边界校验（`api_key_env`/`base_url`/`params` 白名单）在读取密钥环境变量**之前**完成（`lubanagent/model/openai_compat.py:27-35`），已有脏数据定义走该构造入口会被拒绝，不会拿脱敏占位值调用。

```
create_chat_model(definition)
└── default_model_factory.create(definition)
    ├── [未知 factory 名] raise LubanError(CONFIG_ERROR)
    └── [factory == "openai_compat"] create_openai_compatible(definition)
        ├── validate_model_connection(...)  # 先校验后读密钥：api_key_env 格式/base_url 连接边界/params 白名单与秘密键
        │   └── [非法] ValueError → raise LubanError(VALIDATION_ERROR)
        ├── 解析 api_key（校验通过后才读取环境变量）
        │   ├── [api_key_env is None] api_key = "EMPTY"  # ollama 等本地服务
        │   └── [api_key_env 指向变量未设置] raise LubanError(CONFIG_ERROR)
        ├── 解析 base_url = definition.base_url or PROVIDERS[provider]["base_url"]
        ├── 从 params 摘出框架保留字段（_FRAMEWORK_RESERVED_PARAMS：计费字段），避免透传给 ChatOpenAI 报 TypeError
        └── return ChatOpenAI(model=..., api_key=..., base_url=..., **ctor_params)
    └── [factory == "fake"] create_fake(definition)  # 从 params["script"] 构造 ScriptedFakeChatModel
```

## 边界规则

- 密钥只来自环境变量，代码与库只存变量名 `api_key_env`，不存密钥本体（代码强制，来源：`lubanagent/model/openai_compat.py:24-35`；契约文档口径见 `docs/contracts/D1-2-model.md:136`）。
- `api_key_env` 为 `None`（如 `ollama` 本地服务）时向 `ChatOpenAI` 传字面量 `"EMPTY"`（OpenAI 客户端要求非空 key），不视为错误（来源：`lubanagent/model/openai_compat.py:37-38`）。
- `api_key_env` 非空但对应环境变量未设置/为空字符串 → 直接抛 `LubanError(ErrorCode.CONFIG_ERROR)`，不做静默降级（来源：`lubanagent/model/openai_compat.py:40-46`）。
- `params` 字段同时承载模型构造参数（如 `temperature`/`max_tokens`）与框架计费字段（`input_price_per_1k`/`output_price_per_1k`，供 `BudgetGuard`/`lubanagent/observability/cost/pricing.py` 读取），构造 `ChatOpenAI` 前会用 `_FRAMEWORK_RESERVED_PARAMS`（frozenset，当前仅两个计费键）摘除计费字段，否则会被 SDK 当非法参数拒收（来源：`lubanagent/model/openai_compat.py:50-57`）。
- 模型写入/构造统一过 `validate_model_connection` 连接边界（`lubanagent/core/model.py:83-96`），三个入口都覆盖：`ModelDefinition` 的 `model_validator`（`lubanagent/core/model.py:162-170`）、`ModelRepository.upsert_model` 两条写入路径（`lubanagent/persistence/repositories/model.py:15-45`）、`create_openai_compatible` 构造入口先校验后读密钥（`lubanagent/model/openai_compat.py:27-35`）。
- `openai_compat` 的 `params` 白名单收紧为 `_OPENAI_PARAMS`（`lubanagent/core/model.py:36-52`）：此前任意 `params` 会透传给 `ChatOpenAI` 构造并全量回显；存量含自定义 `params` 的定义升级后不可再执行、列表只显示 `[REDACTED]`，需迁移为白名单字段（升级动作见 `UPGRADE.md` §5）。
- `base_url` 只禁凭证（userinfo）/查询参数/片段及密钥形 query 键，不一刀切禁内网——本地 vLLM/Ollama 等 OpenAI 兼容内网地址保留（`lubanagent/core/model.py:67-69` `_unsafe_connection_url`）；模型管理属受信管理面。
- 历史脏数据读取语义：`ModelRepository._to_domain` 用 `ModelDefinition.model_construct` 跳过校验器（`lubanagent/persistence/repositories/model.py:102-114`），脏记录可读取供管理界面安全展示，但执行必须经 `create_openai_compatible` 构造入口再次校验并拒绝（`validate_model_connection` 拒绝含 `[REDACTED]` 占位值的 params）。
- 脱敏函数只产展示副本：`redact_model_params`/`redact_registered_model_params`（`lubanagent/core/model.py:55-64,99-117`）的输出仅用于展示，不得用副本执行；管理界面输出的 `params` 经 `redact_registered_model_params` 脱敏，历史脏数据可展示但秘密不回显。
- `BrainResolver` 不在解析器层缓存映射，每次 `resolve()` 都直接查 `store`；改映射后下一次 `resolve` 立即生效，不发版（代码强制，来源：`lubanagent/model/brain_resolver.py:1,34` docstring；契约验收场景见 `docs/contracts/D1-2-model.md:128`）。
- 能力匹配在 Run 启动前做（非运行中途）：`required_capabilities` 推导规则为 —— `"chat"` 恒需；`agent.tools` 非空 → 追加 `"tool_calling"`；`agent.io.output_schema` 非空 → 追加 `"structured_output"`；`agent.io.input_type == "multimodal"` → 追加 `"vision"`（来源：`lubanagent/model/capability.py:27-36`）。
- 降级是显式策略且必须留痕：`used_fallback=True` 时 `fallback_events` 非空且含一条 `TraceEvent(type=MODEL_FALLBACK)`；反之 `used_fallback=False` 时 `fallback_events=[]`（代码强制不变量，来源：`lubanagent/model/capability.py:81-114`；契约口径见 `docs/contracts/D1-2-model.md:130`）。
- 降级顺序严格按 `agent.brain.fallback_profiles` 声明顺序逐个尝试首个"可解析且能力满足"的槽位；全部失败（含主槽位）→ 抛 `CapabilityMismatchError`，`details` 含 `missing`（最后一次尝试缺失的能力）与 `tried`（已尝试过的 profile 值列表）（来源：`lubanagent/model/capability.py:63-121`）。
- 主槽位解析抛 `BRAIN_NOT_MAPPED` 时同样计入降级链（映射缺失视为不可用，不是硬错误直接抛出）（来源：`lubanagent/model/capability.py:72-77`）。
- `FakeModel`（`ScriptedFakeChatModel`）零密钥、零网络：`script` 三态为 `str`（文本回复）、`dict {"tool_calls": [...]}`（工具调用）、`Exception` 实例（调用时抛出）；`script` 无环境变量开关，触发条件是 `ModelDefinition.factory == "fake"`（显式指定，不是运行时自动探测无密钥后兜底）（来源：`lubanagent/model/fakes.py:20-59`，`__init__.py:22` 预注册 `"fake"` 工厂）。
- `script` 耗尽后再被调用 → `LubanError(ErrorCode.INTERNAL_ERROR, details={"reason": "script exhausted"})`，不做循环重放（来源：`lubanagent/model/fakes.py:48-59`）。
- 模块级注册表 `ModelFactory.register()` 禁止同名重复注册，重复注册直接抛 `LubanError(ErrorCode.CONFIG_ERROR)`（来源：`lubanagent/model/factory.py:18-25`）。
- 分层边界（import-linter 守卫，来源：`importlinter.toml:77-88`）：`lubanagent.model` 禁止依赖 `lubanagent.extension`/`config`/`persistence`/`runtime`/`tool`/`mcp`/`skill`/`knowledge`/`memory`/`graph`/`multiagent`/`observability`/`api`/`app`/`cli`，以及 `sqlalchemy`/`fastapi`。持久化解耦经 `BrainMappingStore` Protocol 完成。

## 名词

| 术语 | 语义 |
|---|---|
| `BrainProfile` | 逻辑大脑四槽位枚举：`fast`/`smart`/`cheap`/`vision`，用于解耦 Agent 定义与具体模型（`lubanagent/core/brain.py:6-12`）。 |
| `ModelDefinition` | 模型注册记录（pydantic 值对象）：`key`+`provider`+`model_name`+`factory`+`capability`+`params`+`base_url`+`api_key_env`+`enabled`；切换模型 = 改库里映射指向另一个 `key`，不改代码不发版；构造即校验连接边界（`lubanagent/core/model.py:133-170`）。 |
| `ModelCapability` | 模型能力声明清单（八字段布尔+两整型），是大脑映射校验与运行前能力校验的依据（`lubanagent/core/model.py:120-130`）。 |
| `BrainResolution` | 一次大脑解析的结果快照：请求的 `profile`、最终 `definition`、是否降级 `used_fallback`、实际命中的 `resolved_profile`、降级留痕 `fallback_events`（`lubanagent/model/brain_resolver.py:22-30`）。 |
| `BrainMappingStore` | 大脑映射存储的 Protocol 抽象（仅一个方法 `get_brain_mapping`），本模块依赖此抽象而非具体 `persistence` 实现，实现分层解耦（`lubanagent/model/brain_resolver.py:15-19`）。 |
| `CapabilityReport` | 一次能力校验结果：`ok` 是否满足、`missing` 缺失能力名列表（`lubanagent/model/capability.py:21-24`）。 |
| `openai_compat` | 内置模型工厂名，对应 `create_openai_compatible`；一个工厂覆盖 DeepSeek/Qwen/GLM/vLLM/Ollama 全部 OpenAI 兼容协议厂商（`lubanagent/model/openai_compat.py`）。 |
| `PROVIDERS` | 内置 provider 预设表：`deepseek`→`https://api.deepseek.com/v1`+`DEEPSEEK_API_KEY`；`qwen`→`https://dashscope.aliyuncs.com/compatible-mode/v1`+`DASHSCOPE_API_KEY`；`glm`→`https://open.bigmodel.cn/api/paas/v4`+`ZHIPUAI_API_KEY`；`vllm`→空（`base_url` 由调用方传入）；`ollama`→`http://localhost:11434/v1`（无密钥）（`lubanagent/model/openai_compat.py:12-21`）。 |
| `fake` | 内置模型工厂名，对应 `create_fake`，构造零密钥零网络的 `ScriptedFakeChatModel`，供单测/CI/演示使用（`lubanagent/model/fakes.py:257-262`）。 |
| `_FRAMEWORK_RESERVED_PARAMS` | `params` 中不透传给底层模型客户端、由框架自身消费的保留字段集合，当前值 `{"input_price_per_1k", "output_price_per_1k"}`（计费单价，供 `BudgetGuard`/成本报表读取）（`lubanagent/model/openai_compat.py:63-65`）。 |
| `validate_model_connection` | 模型入库/构造的统一连接边界校验函数：`api_key_env` 格式、`base_url` 禁凭证/查询/片段、`params` 禁连接覆盖键与秘密键、openai_compat 白名单收口；三个入口（pydantic 校验器/仓储写入/构造工厂）共用同一实现，错误消息不含输入值或密钥（`lubanagent/core/model.py:83-96`）。 |
| `_OPENAI_PARAMS` | `openai_compat` 工厂 `params` 的受控白名单：`temperature`/`max_tokens`/`top_p`/`frequency_penalty`/`presence_penalty`/`n`/`stop`/`seed`/`timeout`/`max_retries`/`streaming` + 两个计费字段；白名单外键（含历史自定义参数）一律拒绝写入与执行（`lubanagent/core/model.py:36-52`）。 |
| `redact_registered_model_params` | 管理界面 `params` 展示脱敏函数：仅展示 openai_compat 白名单推理/计价字段，其余键（含非 openai_compat 工厂全部字段）值替换 `[REDACTED]`；只产展示副本，不用于执行（`lubanagent/core/model.py:55-64`）。 |
| 受控降级 | Agent 主大脑槽位解析失败或能力不满足时，按 `agent.brain.fallback_profiles` 声明顺序自动切换到备用槽位的策略，且必须显式留痕（`MODEL_FALLBACK` 事件），非静默兜底（`lubanagent/model/capability.py:63-121`）。 |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.core` | `from lubanagent.core import ...` | `ModelDefinition`、`ModelCapability`、`BrainProfile`、`AgentDefinition`、`TraceEvent`/`TraceEventType`、`ErrorCode`/`LubanError`、`CapabilityMismatchError` 等纯数据/错误类型（`lubanagent/model/factory.py:7`、`capability.py:6-16`、`brain_resolver.py:6-12`） |
| `langchain_core` / `langchain_openai` | Python import | `BaseChatModel`、`ChatOpenAI`、消息/回调/输出类型，作为底层模型客户端实现（`lubanagent/model/openai_compat.py:6-7`、`fakes.py:8-14`） |
| 调用方注入的 `BrainMappingStore` 实现（运行时通常是 `persistence.ModelRepository`） | `BrainResolver(store, env=...)` 构造时注入 | `get_brain_mapping(env, profile)` 返回的 `ModelDefinition`；本模块只依赖 Protocol，不感知具体实现（`lubanagent/model/brain_resolver.py:15-19,36-38`） |
| 环境变量（`DEEPSEEK_API_KEY`/`DASHSCOPE_API_KEY`/`ZHIPUAI_API_KEY` 等，具体变量名由 `ModelDefinition.api_key_env` 决定） | 进程环境 | 模型 API 密钥（`lubanagent/model/openai_compat.py:29`） |
| 外部模型服务（DeepSeek/通义千问 DashScope/智谱 GLM/vLLM/Ollama 等 OpenAI 兼容 API） | `ChatOpenAI` HTTP 调用（LangChain 内部实现，本模块不感知重试细节） | Chat Completion 请求/响应（`lubanagent/model/openai_compat.py:48`） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent.runtime`（`AgentLoop`） | `BrainResolver(ModelRepository(session), env=...)` + `resolve_brain_for_agent(definition, resolver)` + `create_chat_model(resolution.definition)` | Run 启动前解析大脑槽位为具体模型并构造出可执行 `BaseChatModel`；`resolution.fallback_events` 由 `runtime` 落 Trace，`resolution.definition.key` 回填 `Run.model_key`（`lubanagent/runtime/agent_loop.py:43-47,287-290,437,521`） |
| `lubanagent.cli`（`main.py`） | `from lubanagent.model import BrainResolver, create_chat_model` | CLI 场景下的模型解析与构造（`lubanagent/cli/main.py:29`） |
| `lubanagent.observability.evals`（`replay.py`） | `from lubanagent.model import fake_definition` | 评测重放场景下用 `fake_definition` 构造零密钥模型定义，配合 `ModelRepository.get_brain_mapping` 读原始映射做对比（`lubanagent/observability/evals/replay.py:12,64`） |
| `lubanagent.observability.cost`（`pricing.py`） | 读取 `ModelDefinition.params` 中的 `input_price_per_1k`/`output_price_per_1k` | 成本计算依赖本模块 `ModelDefinition.params` 里的计费字段命名约定（`lubanagent/observability/cost/pricing.py:5-6`，字段来源于本模块 `openai_compat.py:52-55` 的保留字段约定） |
| `lubanagent.api.routes.models` | `ModelRepository`（不直接调用本模块函数，但复用本模块的 `ModelDefinition` 类型做请求/响应模型） | `PUT/GET /models`、`GET/PUT /brain-mappings` 路由用 `ModelDefinition` 作为请求体/依据类型，脱敏后输出 `ModelDefinitionOut`（`lubanagent/api/routes/models.py:1-73`） |
| 外部模型 API（DeepSeek/Qwen/GLM/vLLM/Ollama） | `ChatOpenAI(...).invoke()/.stream()`（由 `runtime` 等上层实际发起调用） | 本模块负责"构造出"客户端实例，实际推理请求由持有该实例的上层（`runtime.AgentLoop`）发起 |
