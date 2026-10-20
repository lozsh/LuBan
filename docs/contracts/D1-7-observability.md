# 契约 D1-7：`lubanagent/observability/trace` + `adapters`

> 来源：开发计划 D1-7、架构设计 §6 全节（双写模型/适配器契约/失败隔离）、AGENTS.md §4（C 方案：内部 DB 是 source of truth，默认 langfuse 自部署，可切 none，不宣传完全等价）。
> 前置：D0-1（core.trace/run/errors）、D0-2（extension.protocols.ObservabilityBackend/TraceHandle、Registry）、D0-3（Settings）、D1-1（Database、RunRepository/TraceRepository、Alembic）。

## 范围

- `lubanagent/observability/`：`__init__.py`
- `lubanagent/observability/trace/`：`writer.py`、`__init__.py`
- `lubanagent/observability/adapters/`：`base.py`、`noop.py`、`langfuse.py`、`langsmith.py`、`registry.py`、`__init__.py`
- `lubanagent/persistence/models/`+`repositories/`：**追加** `export_failures` 表（架构 §6.4）+ `ExportFailureRepository` + Alembic **0002** 迁移
- `lubanagent/extension/`：**扩展** Registry 命名观测后端方法 + Protocol 加 `name`（见「契约决策 1」，同步更新 D0-2 既有测试）
- `tests/lubanagent/observability/`

## 契约决策（先于接口）

1. **ObservabilityBackend 唯一定义仍在 `extension/protocols.py`**，`adapters/base.py` 只 re-export（与 KnowledgeConnector 同策）。同时对 Protocol 做两处演进（架构 §6.2 为依据，框架未发布、演进安全）：
   - 加 `name: str` property（后端标识，注册表键）；
   - `end_trace(self, handle: TraceHandle, status: RunStatus, summary: dict[str, Any] | None = None)`（summary 含 token/cost/end_reason 等汇总，对齐 §6.2 `RunSummary` 语义，dict 承载避免新增 core 类型）。
   - `extension.Registry` 增加：`register_observability_backend(name: str, backend: ObservabilityBackend) -> None`（重复名 → CONFIG_ERROR）、`get_observability_backend(name: str) -> ObservabilityBackend | None`、`list_observability_backends() -> list[str]`；**保留** D0-2 既有 `register_observability/get_observability`（单例覆盖语义：get 优先返回单例覆盖，其次 None——新代码用命名方法）。
2. **`export_failures` 表**（迁移 0002_export_failures）：`id` int pk、`backend` str(64)、`run_id` str(36)、`event_type` str(64)、`payload` JSON、`error` text、`created_at` server_default now、`retried_at` DateTime nullable。index(`backend`)。`ExportFailureRepository`：`record(backend, run_id, event_type, payload, error) -> None`、`list_pending(backend: str | None = None, limit: int = 100) -> list[dict]`、`mark_retried(ids: list[int]) -> None`。
3. 真实 Langfuse/LangSmith 联调不在本单元验收（无服务）；适配器翻译逻辑用**桩 SDK**（monkeypatch 假 langfuse/langsmith 模块）测试，真机验证记入 D1-9/手动联调清单。

## 接口契约

### trace/writer.py（内部 DB 双写的主笔，always on，架构 §6.1）
```python
class TraceWriter:
    """Run/Trace/事件/用量/费用同步落内部 DB（调用方 await，不排队）。评测/成本/工作台的唯一数据源。"""
    def __init__(self, db: Database) -> None
    async def start_run(self, run: Run) -> Trace          # 落 runs 行 + traces 行 + RUN_STARTED 事件
    async def emit(self, event: TraceEvent) -> None       # 落 trace_events 行（trace_id 经 run_id 查）
    async def record_model_usage(self, run_id: str, *, step_index: int | None,
                                 model_key: str, input_tokens: int, output_tokens: int) -> None
    async def record_cost(self, run_id: str, *, model_key: str, cost: float,
                          currency: str = "CNY") -> None
    async def finish_run(self, run: Run) -> None          # 更新 runs 终态字段 + traces 终态 + 终态事件（COMPLETED/FAILED/CANCELLED 按 run.status 选）
```

### adapters/base.py
```python
from lubanagent.extension.protocols import ObservabilityBackend, TraceHandle  # re-export

FailureSink = Callable[[str, str, str, dict[str, Any], str], Awaitable[None]]
    # (backend, run_id, event_type, payload, error_message)

class SafeBackend:
    """失败隔离包装（架构 §6.4）：外发异常不阻断 Run——emit 捕获→记日志→写 sink（限流并发）。"""
    def __init__(self, inner: ObservabilityBackend, *, sink: FailureSink | None = None,
                 max_concurrency: int = 8) -> None   # 内部 asyncio.Semaphore 限流
    @property
    def name(self) -> str                              # f"{inner.name}"
    def start_trace(self, run: Run) -> TraceHandle     # 同步契约，直接委托（建句柄不失败兜底：异常→返回 NoneHandle 并记日志）
    def emit(self, handle: TraceHandle, event: TraceEvent) -> None          # fire-and-forget：asyncio.create_task 内委托，异常→sink+日志
    def end_trace(self, handle: TraceHandle, status: RunStatus,
                  summary: dict[str, Any] | None = None) -> None            # 同步委托，异常→sink+日志
    async def drain(self, timeout: float = 5.0) -> None  # 等飞行中 emit 收尾（Run 结束/进程退出前调用）

def db_failure_sink(db: Database) -> FailureSink      # 写 export_failures 表的标准 sink
```

### adapters/noop.py
```python
class NoopBackend:   # 实现 ObservabilityBackend；name="noop"；三方法全部无操作；start_trace 返回 NoneHandle()
class NoneHandle:    # TraceHandle 实现，空句柄
```

### adapters/langfuse.py / langsmith.py
```python
class LangfuseBackend:    # name="langfuse"
    def __init__(self, *, host: str | None, public_key: str | None, secret_key: str | None) -> None
    # 懒初始化：首次 start_trace 才 import langfuse 并建 client；
    # SDK 未安装或 key 缺失 → self._disabled=True，之后全部方法等同 noop（架构 §6.5：key 缺失自动降级不阻断）
    # 翻译映射（适配器只翻译 core 事件，§6.2）：
    #   start_trace → client.trace(name=run.agent_key, metadata={agent_version, run_id, session_id})
    #   MODEL_CALL_STARTED/MODEL_CALL_COMPLETED → generation（model=model_key, usage）
    #   TOOL_CALL_* → span（name=tool key, input/output）
    #   其余事件 → client.event(name=event.type.value, payload)
    #   end_trace → trace.update(status/ end_reason/ usage/ cost) + flush
class LangSmithBackend:   # name="langsmith"；同构（langsmith SDK 懒加载 + 降级），用 RunTree 翻译
```

### adapters/registry.py
```python
BUILTIN_BACKENDS: dict[str, Callable[[Settings], ObservabilityBackend]]  # {"langfuse": ..., "langsmith": ..., "none": NoopBackend}

def resolve_backend(settings: Settings, registry: Registry | None = None) -> ObservabilityBackend:
    """选址（架构 §6.4）：
    1. settings.observability_backend 为名；
    2. 用户经 registry.register_observability_backend 注册的同名后端优先于内置；
    3. 内置 langfuse/langsmith 由 settings 字段构造；none → NoopBackend；
    4. 未知名 → LubanError(CONFIG_ERROR, details={"backend": ...})；
    5. 返回 SafeBackend 包装后的实例（失败隔离默认生效；sink 由装配层 D1-3 接 db_failure_sink，本单元 sink=None 即记日志）。
    """
```

### observability/__init__.py
re-export：TraceWriter、SafeBackend、NoopBackend、LangfuseBackend、LangSmithBackend、resolve_backend、db_failure_sink、BUILTIN_BACKENDS。

## 行为契约

1. 内部 DB 始终 source of truth：TraceWriter 不依赖任何外部后端即可完整落库（代码强制；测试断言一次 Run 的 runs/traces/trace_events/token_usages/cost_records 五表数据能回答七问：版本/模型/Prompt 痕迹（payload）/工具调用/Token/费用/结束原因）。
2. 外发失败不阻断 Run：`SafeBackend.emit` 在内层抛错时返回正常，且 sink 收到记录（代码强制，测试用爆炸桩后端断言）。
3. 降级显式：langfuse key 缺失 → 退化为 noop 行为 + warning 日志，不抛错（代码强制）。
4. 用户注册优先：resolve_backend 命中用户注册名 → 返回用户实例（§6.4，代码强制）。

## 边界契约

- observability 可 import core/extension/config/persistence；禁止 import model/runtime/api/app.*/langchain*（langfuse/langsmith SDK 只在对应适配器函数体内懒 import）。
- 内部 DB 双写不以外部后端可用为前提（AGENTS.md §4 C 方案）。
- 不宣传"完全等价切换"——本单元 docs/注释不做此承诺（§6.6）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/observability/` 全绿：
   - TraceWriter：start→emit→record_model_usage→record_cost→finish 后，五表内容与输入一致（tmp SQLite）；
   - 七问测试：构造含模型调用 + 工具调用 + 结束原因的 Run，从 DB 读回能回答全部七项；
   - SafeBackend：内层 emit 抛错 → 不传播 + sink 记录含 backend/run_id/event_type；并发 20 个 emit 时限流信号量生效（max_concurrency 峰值断言）；
   - resolve_backend：`none` → NoopBackend；`langfuse` 无 key → 行为 noop 化 + warning；`OBSERVABILITY_BACKEND=mycorp` + 用户注册 "mycorp" → 返回用户实例；未知名 → CONFIG_ERROR；
   - Langfuse/LangSmith 翻译：monkeypatch 桩 SDK（记录调用）→ start_trace/emit/end_trace 的翻译调用序列与映射表一致；SDK import 失败路径 → 退化 noop 不抛错；
   - 扩展的 Registry 命名方法 + Protocol `name`/`summary` 演进：D0-2 既有测试同步更新后全绿；
   - `alembic upgrade head` 含 0002；ExportFailureRepository record/list_pending/mark_retried 一轮。
2. 全量回归 `pytest` 绿；`ruff check lubanagent` 无错；`lint-imports` 8 契约仍 KEPT（本单元不新增越界依赖）。

## 存疑项与决策

1. Protocol 演进（name/summary）与 Registry 命名方法 → 见「契约决策 1」（2026-07-23）。
2. `export_failures` 重试器（定时重放）spec 未要求 → 本单元只落表与仓储，重放任务待 D1-9/运维脚本补齐（记 TODO）。
3. emit 异步化方案 → 决策：fire-and-forget + Semaphore 限流 + drain 收尾（架构"异步+限流+降级"的最小代码强制实现）。
