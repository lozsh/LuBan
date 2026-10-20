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

# observability-adapters 模块

## 定位

observability-adapters 模块（`lubanagent/observability/adapters/`）负责把内部统一的 `TraceEvent` 事件流可插拔地翻译并外发到外部观测后端（Langfuse / LangSmith），并对外发失败做隔离，保证外部后端故障不影响核心 Run 执行。自 H07 起，`SafeBackend` 在外发前对事件 payload 与 `end_trace` summary 构造脱敏副本（秘密不外发第三方观测平台），且 `resolve_backend` 统一 `SafeBackend` 包装——本模块是观测数据外发三方平台的唯一出口。核心 Protocol 是 `ObservabilityBackend`（定义在 `lubanagent/extension/protocols.py:57-64`，本模块 re-export，见 `lubanagent/observability/adapters/base.py:16-19`），无独立数据库表聚合根；本模块唯一直接落库的产出是失败留痕表 `export_failures`（`lubanagent/persistence/models/trace.py:68-88`，落库 payload 同样是脱敏副本）。

本模块与"内部 DB 落库"（`lubanagent.observability.trace.TraceWriter`，见 `lubanagent/observability/trace/writer.py`）是并列姐妹关系而非上下游调用：二者由 `AgentRuntime` 在同一批事件上分别调用（`lubanagent/runtime/agent_loop.py:313-320`），内部 DB 写入 always on，本模块的外发是 optional、可关闭（架构设计 §6.1）。

## 接口契约

### HTTP

无（本模块不直接暴露 HTTP 接口；观测数据经 `lubanagent/api` 层的 Trace 查询接口读取内部 DB，不读本模块）。

### RPC / MQ / Cron

无 RPC / MQ / Cron 接口。本模块对外的契约是 Python 内部 Protocol：

| 方法 | 签名 | 说明 | 错误情况 |
|---|---|---|---|
| `ObservabilityBackend.name` | `@property def name(self) -> str` | 后端标识，也是 registry 注册键（`lubanagent/extension/protocols.py:58-59`） | 无 |
| `ObservabilityBackend.start_trace` | `def start_trace(self, run: Run) -> TraceHandle` | 开启一次 Run 的外部 trace，返回后端专属句柄（`lubanagent/extension/protocols.py:60`） | 后端实现内部异常；经 `SafeBackend` 包装后不外抛，见"边界规则" |
| `ObservabilityBackend.emit` | `def emit(self, handle: TraceHandle, event: TraceEvent) -> None` | 翻译单条 `TraceEvent` 到后端调用（`lubanagent/extension/protocols.py:61`）；经 `SafeBackend` 包装后 inner 收到的是 payload 脱敏副本 | 同上 |
| `ObservabilityBackend.end_trace` | `def end_trace(self, handle, status: RunStatus, summary: dict | None = None) -> None` | 收尾一次 Run 的外部 trace，summary 含 token/cost/end_reason（`lubanagent/extension/protocols.py:62-64`） | 同上 |
| `resolve_backend` | `def resolve_backend(settings: Settings, registry: Registry | None = None) -> ObservabilityBackend`（`lubanagent/observability/adapters/registry.py:41-62`） | 按 `settings.observability_backend` 选址并返回 `SafeBackend` 包装实例——观测数据外发三方平台的唯一出口（H07） | 未知后端名 → `LubanError(ErrorCode.CONFIG_ERROR)`（`registry.py:58-61`） |
| `SafeBackend._redact_event`（模块级，`base.py:26-35`） | `(event: TraceEvent) -> TraceEvent` | 构造 payload 经 `redact_value` 脱敏后的事件副本，不改调用方原事件 | 无 |
| `SafeBackend.emit` | `def emit(self, handle, event) -> None`（`base.py:107-122`） | 外发前 `event = _redact_event(event)`（`base.py:113`），inner 与失败上报 sink 均收脱敏副本；之后 fire-and-forget 委托 | 内层异常不外抛，见"边界规则" |
| `SafeBackend.end_trace` | `def end_trace(self, handle, status, summary=None) -> None`（`base.py:124-138`） | summary 经 `redact_value` 脱敏后同时传 inner 与失败上报（`safe_summary`，`base.py:128-136`） | 异常不外抛，走 sink |
| `SafeBackend.set_sink` | `def set_sink(self, sink: FailureSink | None) -> None`（`lubanagent/observability/adapters/base.py:89-91`） | 装配层注入失败留痕回调 | 无 |
| `SafeBackend.set_retry_db` | `def set_retry_db(self, db: Database) -> None`（`base.py:93-95`） | 装配层注入重试数据源：drain 时回放 `export_failures` 待重试记录（`factory.py:84` 注入） | 无 |
| `SafeBackend.drain` | `async def drain(self, timeout: float = 5.0) -> None`（`base.py:140-155`） | 等待飞行中的 emit 任务收尾（Run 结束/进程退出前调用）；收尾顺带两件事：把同步上下文攒下的失败落 sink（`_flush_inline_failures`），再回放待重试的外发失败（`_sweep_failures`，见"行为"） | 超时 → 只记警告日志，不抛异常（`base.py:152-154`）；重试消费自身异常同样只记日志不阻断 drain（`base.py:257-318` 尾部 try/except） |
| `SafeBackend._sweep_failures`（内部，经 drain 触发） | `async () -> None`（`base.py:257-318`） | 回放待重试留痕：取 `list_pending`（批 100）逐条 `inner.replay(...)`——成功删记录、失败 `retry_count+1`、达上限（默认 3，`DEFAULT_MAX_ATTEMPTS`）放弃并保留行供运维查询；回放 payload 为留痕时的脱敏副本（安全重放拒绝语义属 H10 待落地） | 后端未实现 `replay` 方法 → 只告警一次并跳过自动重试（`base.py:261-269`） |
| 内置适配器 `replay` | `def replay(self, run_id: str, event_type: str, payload: dict) -> None`（`langfuse.py:116-126`、`langsmith.py:127`、`noop.py:29`） | 回放历史失败事件：原句柄已失（跨进程），按通用 event 重发、run_id 进 metadata；langfuse 已降级 noop 时抛 `RuntimeError`（无法回放） | 非 Protocol 强制方法——`SafeBackend` 用 `getattr(self._inner, "replay", None)` 探测，自定义后端可不实现（此时留痕不自动重试，可经仓储手动回放） |
| `INLINE_BUFFER_LIMIT` | 常量 `1000`（`base.py:40-41`） | 同步上下文失败内存缓冲上限：超出丢最旧并记日志（正常路径 emit/drain 会及时落库） | — |
| `db_failure_sink` | `def db_failure_sink(db: Database) -> FailureSink`（`lubanagent/observability/adapters/base.py:44-55`） | 构造写 `export_failures` 表的标准 sink | 无（写库异常由调用处 `_report_async` 捕获，见 `base.py:220-223`） |

## 数据模型

### export_failures

对应实体：`ExportFailureRow`（`lubanagent/persistence/models/trace.py:68-88`；`retry_count` 列由迁移 `0007_export_failures_retry_count.py` 新增）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id（INTEGER） | id（int） | ✓ | 自增 | 主键 |
| backend（VARCHAR(64)） | backend（str） | ✓ | — | 后端标识（如 `langfuse`/`langsmith`），来自 `SafeBackend.name`（`base.py:54-56`） |
| run_id（VARCHAR(36)） | run_id（str） | ✓ | — | 对应 Run ID |
| event_type（VARCHAR(64)） | event_type（str） | ✓ | — | 失败发生时的事件类型字符串（`TraceEventType.value` 或 `"end_trace"`，见 `base.py:96,140` ） |
| payload（JSON） | payload（dict[str, Any]） | 否 | `{}` | 失败时的事件载荷（脱敏副本：`emit` 入口已 `_redact_event`，或 `end_trace` 的 `safe_summary`，见 `base.py:113,136,168,183`） |
| error（TEXT） | error（str） | ✓ | — | 异常信息 `str(exc)`（`base.py:137,169,184`） |
| created_at（DATETIME） | created_at（datetime） | ✓ | `func.now()` | 落库时间 |
| retried_at（DATETIME） | retried_at（datetime \| None） | 否 | `NULL` | 最后一次重试尝试时间，`ExportFailureRepository.mark_retry_failed` 写入（`lubanagent/persistence/repositories/export_failure.py:73-86`） |
| retry_count（INTEGER） | retry_count（int） | ✓ | `0` | 重试尝试失败次数；达上限（`DEFAULT_MAX_ATTEMPTS=3`）即放弃——行保留供运维查询（`list_given_up`），不再自动重试（`models/trace.py:88`、`export_failure.py:11-12`） |

索引：
- `ix_export_failures_backend (backend)`（`lubanagent/persistence/models/trace.py:72`） — 按后端过滤待重试记录

## 行为

### 状态机

无（本模块不维护业务状态机；`TraceHandle` 是无状态的一次性句柄。`export_failures` 行的重试状态由 `retry_count` 计数承载：`0..max_attempts-1` 为待重试（`list_pending` 取 `retry_count < max_attempts`），每次回放失败 `+1` 并刷新 `retried_at`，达 `DEFAULT_MAX_ATTEMPTS=3` 即放弃（行保留供 `list_given_up` 运维查询，不再自动重试）；回放成功则删除该行（`export_failure.py:43-95`）。）

### 调用树

#### 后端选址（应用启动装配期）

`lubanagent/app/factory.py:58-61` 在应用启动时调用 `resolve_backend`，选出的实例统一注入 `AgentRuntime`；`AgentRuntime.__init__` 若未显式传入 observability 也会自行兜底调用 `resolve_backend`（`lubanagent/runtime/agent_loop.py:168-174`）。

```
resolve_backend(settings, registry)  # lubanagent/observability/adapters/registry.py:41
├── name = settings.observability_backend  # 读 OBSERVABILITY_BACKEND env
├── [registry 非空] registry.get_observability_backend(name)  # 用户注册优先
│   └── 命中 → 直接 SafeBackend(user_backend) 返回，不再看内置表
├── [未命中/registry 为空] BUILTIN_BACKENDS.get(name)  # 查内置表 {langfuse, langsmith, none}
│   ├── 命中 langfuse → _build_langfuse(settings) 构造 LangfuseBackend（host/public_key/secret_key 直传，不在此处校验 key）
│   ├── 命中 langsmith → _build_langsmith(settings) 构造 LangSmithBackend
│   └── 命中 none → _build_none(settings) 构造 NoopBackend
├── [三者均未命中] raise LubanError(ErrorCode.CONFIG_ERROR, "未知观测后端: {name}")
└── 返回 SafeBackend(inner)  # 统一失败隔离包装；sink 此处未挂，需装配层显式 set_sink
```

装配层紧接着调用 `observability.set_sink(db_failure_sink(db))` 与 `observability.set_retry_db(db)`（`lubanagent/app/factory.py:83-84`），把失败留痕 sink 与重试数据源接到 `export_failures` 表；`resolve_backend` 自身**不**挂 sink（`registry.py:50` 注释明示）。

#### emit 失败降级调用树（Run 执行期）

`AgentRuntime` 每处理一条 `TraceEvent` 都并行调用 `self._trace_writer.emit(event)`（内部 DB，独立、不受本模块影响）与 `self._obs.emit(handle, event)`（本模块，外部后端），见 `lubanagent/runtime/agent_loop.py:313-320`。`self._obs` 是 `SafeBackend` 实例。

```
SafeBackend.emit(handle, event)  # base.py:107
├── event = _redact_event(event)  # base.py:113：payload 脱敏副本，inner 与 sink 均只见脱敏数据
├── 尝试 asyncio.get_running_loop()
│   ├── 有运行中的 loop → 建 asyncio.Task 调度 _emit_guarded(handle, event)，立即返回（fire-and-forget，不阻塞调用方）
│   │   └── _emit_guarded  # base.py:172
│   │       ├── async with self._sem:（Semaphore(max_concurrency=8) 限流）
│   │       ├── self._inflight += 1
│   │       ├── inner.emit(handle, event)  # 真正调用 LangfuseBackend/LangSmithBackend/NoopBackend/用户后端（收到脱敏 payload）
│   │       ├── [抛异常] except Exception as exc:
│   │       │   ├── logger.warning("观测后端 %s emit 失败", ...)  # 只记日志，不重新抛出
│   │       │   └── await self._report_async(run_id, event_type=event.type.value, payload=event.payload, error=str(exc))
│   │       │       └── await self._sink(name, run_id, event_type, payload, error)  # db_failure_sink → 写 export_failures 表（同样脱敏副本）
│   │       │           └── [sink 自身异常] except Exception: logger.warning(...)（双保险，不再上抛）
│   │       └── finally: self._inflight -= 1
│   └── 无运行中的 loop（同步上下文） → _emit_inline(handle, event)  # base.py:158-170
│       ├── inner.emit(handle, event)
│       └── [抛异常] except Exception: logger.warning(...) + _buffer_failure(...)
│           # 失败先入内存缓冲（上限 INLINE_BUFFER_LIMIT=1000，超限丢最旧并记日志，base.py:225-245），
│           # 下次有运行 loop 时（emit 调用或 drain）经 _flush_inline_failures 调度到 sink 落 export_failures（base.py:119,145,247-255）
└── 全程无异常向调用方传播，AgentRuntime 主循环不受影响
```

#### 缺 key 自动降级为 noop（首次调用触发）

Langfuse/LangSmith 适配器都在首次 `start_trace` 时懒加载 SDK 与校验 key，而非构造期：

```
LangfuseBackend.start_trace(run)  # langfuse.py:40
└── self._ensure_client()  # langfuse.py:116
    ├── [self._disabled 已 True] 直接返回 None
    ├── [public_key/secret_key 任一缺失] logger.warning("langfuse key 缺失，观测外发降级为 noop（内部 DB 不受影响）"); self._disabled = True; return None
    ├── [import langfuse 失败] logger.warning("langfuse SDK 未安装，...降级为 noop..."); self._disabled = True; return None
    └── [均通过] 构造 langfuse.Langfuse(...) 客户端并缓存
LangfuseBackend.start_trace 返回值：client is None → NoneHandle(run_id=run.id)（langfuse.py:42-43）
LangfuseBackend.emit/end_trace：isinstance(handle, LangfuseHandle) 为 False（即拿到 NoneHandle）→ 直接 return，等同 noop（langfuse.py:55-56,104-105）
```

`LangSmithBackend` 逻辑对称，见 `_ensure_run_tree`（`langsmith.py:127-143`）：key 缺失或 `import langsmith` 失败同样置 `self._disabled = True` 并返回 `None`，`start_trace` 随即返回 `NoneHandle`。

#### drain 收尾重试回放（风险 R3/R4 修复：export_failures 从只写不读到自动消费）

应用关闭（FastAPI lifespan）调用 `drain()` 时，在飞行中 emit 收尾后追加一轮重试消费（`base.py:257-318` `_sweep_failures`）：

```
drain()  # base.py:140
├── _flush_inline_failures()  # 同步上下文攒下的缓冲失败先调度到 sink 落库
├── [逐批] 等待飞行中的 emit 任务收尾（超时 5s 只记日志返回）
└── _sweep_failures()  # base.py:257，前置条件：set_retry_db(db) 已注入（factory.py:84）
    ├── replay = getattr(self._inner, "replay", None)
    │   └── [None（自定义后端未实现）] 只告警一次并跳过自动重试（可经仓储查询手动回放）
    ├── pending = ExportFailureRepository.list_pending(backend=self.name, limit=100, max_attempts=3)
    │   # 取 retry_count < max_attempts 的待重试行，按 id 升序
    ├── for row in pending: inner.replay(row.run_id, row.event_type, row.payload)
    │   ├── 成功 → 记入 succeeded
    │   └── 抛异常 → 记入 failed（重试失败，计第 retry_count+1 次尝试）
    ├── ExportFailureRepository.delete(succeeded)  # 回放成功 → 删行（消费闭环终点）
    ├── ExportFailureRepository.mark_retry_failed(failed)  # retry_count+1、retried_at=now
    └── [retry_count+1 >= 3] logger.warning("重试达上限放弃（行保留供运维查询）")
    # 整个 _sweep_failures 包在 try/except 内：重试消费自身异常只记日志，不阻断 drain/Run 收尾
```

内置三个适配器（langfuse/langsmith/noop）均已实现 `replay(run_id, event_type, payload)`：原句柄已失（跨进程），按通用 event 重发、`run_id` 进 metadata（`langfuse.py:116-126`、`langsmith.py:127`、`noop.py:29`）；langfuse 已因缺 key 降级 noop 时 replay 抛 `RuntimeError`（该次重试计失败，下轮 drain 再试直至达上限）。注意：回放的 payload 是留痕时的脱敏副本（H07 起 `emit` 入口先脱敏再入留痕），安全重放遇含 `[REDACTED]` 的记录须显式拒绝——该拒绝语义属 H10 待落地。

## 边界规则

- **外部后端故障绝不阻断 Run**：`SafeBackend.emit` 对内层异常全量捕获且不重新抛出（`lubanagent/observability/adapters/base.py:178-185` 与 `158-170`），`end_trace` 同样捕获（`base.py:124-138`），`start_trace` 异常降级为 `NoneHandle`（`base.py:97-105`）。这是本模块最核心的边界，测试 `tests/lubanagent/observability/test_safe_backend.py` 的 `test_emit_failure_isolated_and_sink_notified`、`test_end_trace_failure_goes_to_sink`、`test_start_trace_failure_returns_none_handle` 三条用例直接验证该行为。
- **观测外发唯一出口，秘密不外发第三方平台（H07）**：`resolve_backend` 统一返回 `SafeBackend` 包装实例，且 `AgentRuntime.__init__` 对显式注入的非 `SafeBackend` 后端也再包一层 `SafeBackend`（`runtime/agent_loop.py:183-187`）——框架内所有观测外发均经脱敏；`emit` 外发前构造 payload 脱敏副本（`base.py:113`，inner 与失败上报 sink 均收脱敏数据）、`end_trace` 的 summary 经 `redact_value` 脱敏后同时传 inner 与失败上报（`base.py:128-136`）；脱敏规则复用 `lubanagent.core.redact`（字段级秘密键 + 凭证形态 URL/bearer 字符串，详见 core-extension-config 模块）。边界：用户绕过 Runtime 直接调用自有 backend 对象不受此保护。测试：`tests/lubanagent/observability/test_safe_backend.py`（外发 payload/summary 脱敏）。
- **emit 是 fire-and-forget，不等待外部后端返回**：有运行中事件循环时用 `loop.create_task` 调度（`base.py:115-122`），调用方（`AgentRuntime`）不 await 外部后端网络往返，只 await 内部 DB 写入（`agent_loop.py:313-320`）。
- **失败必须留痕，写 export_failures 表，但留痕本身也不允许炸主流程**：异步路径下 `_report_async` 内部再包一层 try/except（`base.py:220-223`），即使 sink 自身（DB 写入）失败也只记日志。
- **限流上限 `max_concurrency=8`（默认），用信号量控制并发 emit 数**（`base.py:76,176`），防止外部后端拖垮进程；测试 `test_emit_concurrency_limited_by_semaphore` 验证峰值恰为设定值。
- **同步上下文失败留痕走内存缓冲，最终一致**：若调用 `emit` 时不存在运行中的事件循环，走 `_emit_inline`，失败先入内存缓冲 `_inline_failures`（上限 `INLINE_BUFFER_LIMIT=1000`，超限丢最旧并记日志），下次有运行 loop 时（emit 调用或 drain）经 `_flush_inline_failures` 调度到 sink 落 `export_failures` 表（`base.py:158-170、225-255`）。进程在缓冲落库前崩溃才会丢这部分留痕（这是剩余风险面）；无 sink 时缓冲无意义，只留结构化日志（`base.py:229-234`）。
- **缺 key 或 SDK 未安装自动降级为 noop，不抛异常阻断启动**：`LangfuseBackend._ensure_client`（`langfuse.py:116-136`）与 `LangSmithBackend._ensure_run_tree`（`langsmith.py:127-143`）判断逻辑一致，`_disabled` 置位后一次性生效，后续调用直接短路返回 `None`/跳过。
- **未知后端名是唯一会向上抛出异常的路径**：`resolve_backend` 在 `settings.observability_backend` 既非用户注册名、也非内置 `{langfuse, langsmith, none}` 时抛 `LubanError(ErrorCode.CONFIG_ERROR)`（`registry.py:57-61`），属配置期错误而非运行期外发失败，两者不可混淆。
- **用户注册后端优先于内置同名后端**：`resolve_backend` 先查 `registry.get_observability_backend(name)`，命中即返回，不再查 `BUILTIN_BACKENDS`（`registry.py:53-56`）；因此用户若注册名为 `"langfuse"` 的自定义后端，会完全屏蔽内置 Langfuse 适配器（代码允许，无重名保护，重名保护只在 `Registry.register_observability_backend` 对**同名两次注册**报错，见 `lubanagent/extension/registry.py:84-87`，不约束"用户名与内置名相同"这一情形，此为 `[TODO: 未在代码或文档中找到该场景的显式规则或警告]`）。
- **`export_failures` 的消费闭环已接通（drain 驱动）**：写入方是 `db_failure_sink`；消费方是 `SafeBackend._sweep_failures`（drain 收尾触发，`factory.py:84` 经 `set_retry_db` 注入数据源）——`list_pending` 取待重试、回放成功 `delete`、失败 `mark_retry_failed` 计次、达 `DEFAULT_MAX_ATTEMPTS=3` 放弃（行保留供 `list_given_up` 运维查询）（`base.py:257-318`、`export_failure.py:43-95`）。自定义后端未实现 `replay` 方法时不自动重试（只告警一次），可经仓储手动回放。

## 名词

| 术语 | 语义 |
|---|---|
| ObservabilityBackend | Protocol，定义 `name`/`start_trace`/`emit`/`end_trace` 三方法契约，是本模块所有适配器与 `SafeBackend` 包装的统一接口（`lubanagent/extension/protocols.py:57-64`） |
| SafeBackend | 对任意 `ObservabilityBackend` 实现的失败隔离包装类：外发前 payload/summary 脱敏（H07）、异步化 emit、信号量限流、异常捕获+落库留痕、同步上下文失败缓冲（上限 1000）、drain 收尾重试回放（`lubanagent/observability/adapters/base.py:58-95`） |
| TraceHandle | Protocol，后端不透明句柄，各适配器自定义具体类型（`NoneHandle`/`LangfuseHandle`/`LangSmithHandle`），仅用于在一次 Run 生命周期内串联 start_trace→emit→end_trace（`lubanagent/extension/protocols.py:52-53`） |
| export_failures | 外发失败留痕表，记录 backend/run_id/event_type/payload/error/retried_at/retry_count，消费闭环已接通：`SafeBackend.drain` 收尾回放待重试记录（成功删/失败计次/默认 3 次达上限放弃），已放弃行经 `list_given_up` 供运维查询；H07 起 payload 为脱敏副本（`lubanagent/persistence/models/trace.py:68-88`、`base.py:257-318`） |
| 降级为 noop | 两种触发场景：(1) `OBSERVABILITY_BACKEND=none` 直接选中 `NoopBackend`；(2) Langfuse/LangSmith 因 key 缺失或 SDK 未安装，在首次 `start_trace` 时把自身 `_disabled` 置 True 并返回 `NoneHandle`，效果等同 noop 但适配器实例仍是 Langfuse/LangSmith 类型（`langfuse.py:116-136`、`langsmith.py:127-143`） |
| 两国集成深度不等价 | 架构设计 §6.6 的诚实说明：LangSmith 对 LangGraph 原生集成最深（step 级细粒度自动生成），Langfuse 走 OTel 标准协议，细粒度依赖本模块适配器主动映射事件类型，默认粒度略粗；两者在"Run 七问"（Agent版本/模型/Prompt/工具调用/Token/费用/结束原因）上一致，因为这些由内部 DB 保证，与外部后端选择无关 |
| fire-and-forget | `SafeBackend.emit` 的调用语义：有运行中事件循环时创建 task 后立即返回，不等待外部后端网络调用完成（`base.py:115-122`） |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.config.Settings` | `settings.observability_backend`/`langfuse_*`/`langsmith_*` 字段（`lubanagent/config/settings.py:27-32`） | 选址名与各后端连接参数（host/public_key/secret_key/api_key/project），经 `OBSERVABILITY_BACKEND` 等 env 注入 |
| `lubanagent.extension.Registry` | `registry.get_observability_backend(name)`（`lubanagent/extension/registry.py:89-90`） | 用户经 `register_observability_backend` 注册的自定义后端实例，选址时优先于内置 |
| `lubanagent.core`（`Run`/`RunStatus`/`TraceEvent`/`TraceEventType`） | `lubanagent/core/trace.py:12-38`、`lubanagent/core/run.py` | Run 元信息（agent_key/agent_version/session_id 等）与 19 种 `TraceEventType` 枚举值，作为适配器翻译的输入契约 |
| `lubanagent.persistence`（`Database`/`ExportFailureRepository`） | `db_failure_sink(db)`（`base.py:44-55`） | 失败落库的会话与仓储实现 |
| `lubanagent.core.redact`（共享脱敏组件） | `redact_value`（`base.py:15`） | `emit`/`end_trace` 外发前构造脱敏副本的规则来源（字段级秘密键 + 凭证形态字符串识别） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| Langfuse 自部署服务（外部 HTTP） | `langfuse.Langfuse` SDK 客户端（`langfuse.py:126-136`），`client.trace/generation/span/event/flush` | 把 Run 的 model_call/tool_call/自定义事件翻译为 Langfuse trace/generation/span 调用 |
| LangSmith 云服务（外部 HTTP） | `langsmith.RunTree` SDK（`langsmith.py:137-143`），`root.post/create_child/end/patch` | 把 Run 的事件翻译为 LangSmith RunTree 层级调用 |
| `export_failures` 表 | `ExportFailureRepository.record`（`export_failure.py:24-41`），经 `db_failure_sink` 回调触发 | 外发失败时落一行留痕记录；drain 收尾经 `list_pending`/`delete`/`mark_retry_failed` 消费（见"行为"节重试回放调用树） |
| `lubanagent.runtime.AgentRuntime` | `self._obs.start_trace/emit/end_trace/drain`（`lubanagent/runtime/agent_loop.py:169-174,317-320,345,697-706`） | `AgentRuntime` 持有 `SafeBackend` 实例作为观测出口之一（与 `TraceWriter` 并列），Run 生命周期内驱动本模块三个核心方法 |
| `lubanagent.app.factory` | `resolve_backend` + `observability.set_sink`/`set_retry_db`/`drain`（`lubanagent/app/factory.py:81-84、133-135`） | 应用启动时选址装配并注入失败 sink 与重试数据源，应用关闭（FastAPI lifespan）时调用 `drain()` 等待外发收尾并回放待重试留痕 |
