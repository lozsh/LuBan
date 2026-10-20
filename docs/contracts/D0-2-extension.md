# 契约 D0-2：`lubanagent/extension` 扩展点 Protocol + Registry + 装饰器

> 来源：开发计划 D0-2、架构设计 §3.2/§4.2/§3.3、AGENTS.md §3/§4（渠道层三扩展点、领地隔离）。
> 前置：D0-1 已完成（`lubanagent/core/` 可用，公开名见其 `__init__.py`）。

## 范围

`lubanagent/extension/`：`protocols.py`、`registry.py`、`decorators.py`、`__init__.py` + `tests/lubanagent/extension/`。

## 接口契约

### protocols.py（typing.Protocol，用户实现接口而非继承基类）
`runtime_checkable` 全部加上。契约内小模型用 Pydantic v2。

```python
class GraphDescriptor(BaseModel):   # 契约决策：core 无图描述符，定义在此
    name: str
    description: str = ""
    builder: Callable[..., Any]     # 图构建函数（返回 LangGraph 编译图；extension 不 import langgraph，Any 承载）

class Principal(BaseModel):         # 契约决策：框架中立的鉴权主体
    subject: str
    scopes: list[str] = []
    claims: dict[str, Any] = {}

class AuthRequest(BaseModel):       # 契约决策：不 import fastapi，api 层负责适配
    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}

class ToolProvider(Protocol):
    def tools(self) -> list[ToolDescriptor]: ...

class GraphProvider(Protocol):
    def graphs(self) -> list[GraphDescriptor]: ...

class AuthProvider(Protocol):
    async def verify(self, request: AuthRequest) -> Principal: ...
    # 失败抛 LubanError(ErrorCode.AUTH_FAILED)

class TraceHandle(Protocol):        # 后端不透明句柄
    pass

class ObservabilityBackend(Protocol):
    def start_trace(self, run: Run) -> TraceHandle: ...
    def emit(self, handle: TraceHandle, event: TraceEvent) -> None: ...
    def end_trace(self, handle: TraceHandle, status: RunStatus) -> None: ...

class Hook(Protocol):               # 契约决策：async（运行期全 async，技术方案 §8）
    async def pre_run(self, ctx: RunContext) -> None: ...
    async def pre_tool(self, ctx: RunContext, tool: ToolDescriptor, args: dict[str, Any]) -> None: ...
    async def post_step(self, ctx: RunContext, step: Step) -> None: ...

class Snippet(BaseModel):
    content: str
    source: str
    score: float = 0.0
    metadata: dict[str, Any] = {}

class KnowledgeConnector(Protocol):  # 契约决策：唯一定义在此，D3-1 knowledge/connector.py 仅 re-export
    async def retrieve(self, query: str, top_k: int) -> list[Snippet]: ...

# ---- 渠道层三扩展点（开源核心定义、商用渠道层实现，AGENTS.md §3）----
class SessionState(BaseModel):
    session_key: str
    state: str                        # 状态机值由渠道层定义（如 ai_serving/human_pending/human_serving）
    data: dict[str, Any] = {}
    updated_at: datetime

class SessionStateStore(Protocol):
    async def get(self, session_key: str) -> SessionState | None: ...
    async def save(self, state: SessionState) -> None: ...

class GuardVerdict(BaseModel):
    allowed: bool
    reason: str = ""
    sanitized_text: str | None = None  # 可选：改写后的安全文本

class OutputGuard(Protocol):
    async def check(self, text: str, *, context: dict[str, Any]) -> GuardVerdict: ...

class ChannelConnector(Protocol):
    @property
    def name(self) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

### registry.py
```python
@dataclass(frozen=True)
class RegisteredTool:
    descriptor: ToolDescriptor
    handler: Callable[..., Any]

class Registry:
    def __init__(self) -> None
    # tool
    def register_tool(self, descriptor: ToolDescriptor, handler: Callable[..., Any]) -> None   # key 重复 → LubanError(CONFIG_ERROR)
    def get_tool(self, key: str) -> RegisteredTool                                            # 不存在 → LubanError(TOOL_NOT_FOUND)
    def has_tool(self, key: str) -> bool
    def list_tools(self) -> list[RegisteredTool]
    # graph
    def register_graph(self, descriptor: GraphDescriptor) -> None                             # name 重复 → CONFIG_ERROR
    def get_graph(self, name: str) -> GraphDescriptor                                         # 不存在 → LubanError(VALIDATION_ERROR)
    def list_graphs(self) -> list[GraphDescriptor]
    # auth（不注册 → None，框架默认 API Key，D1-3 落地）
    def register_auth(self, provider: AuthProvider) -> None
    def get_auth(self) -> AuthProvider | None
    # observability（不注册 → None，由 settings.observability_backend 选择，D1-7 落地）
    def register_observability(self, backend: ObservabilityBackend) -> None
    def get_observability(self) -> ObservabilityBackend | None
    # hooks
    def register_hook(self, hook: Hook) -> None
    def hooks(self) -> list[Hook]
    # knowledge
    def register_knowledge(self, name: str, connector: KnowledgeConnector) -> None            # name 重复 → CONFIG_ERROR
    def get_knowledge(self, name: str) -> KnowledgeConnector                                  # 不存在 → LubanError(VALIDATION_ERROR)
    def list_knowledge(self) -> list[str]

default_registry = Registry()   # 模块级默认实例
```

### decorators.py
```python
def tool(*, key: str | None = None, description: str = "", risk: RiskLevel = RiskLevel.LOW,
         timeout_seconds: float = 30.0, max_retries: int = 0, idempotent: bool = False,
         approval_required: bool = False, registry: Registry | None = None) -> Callable:
    """装饰函数 → 生成 ToolDescriptor 并注册（默认 default_registry），返回原函数（保持可直接调用/单测）。
    - key 缺省 = 函数名
    - args_schema：从函数签名 + type hints 经 pydantic create_model 生成 JSON Schema
    - signature_hash：inspect.signature 规范化字符串的 sha256 hex 前 16 字符
    """

def graph(*, name: str | None = None, description: str = "", registry: Registry | None = None) -> Callable:
    """装饰图构建函数 → 生成 GraphDescriptor 注册，返回原函数。name 缺省 = 函数名。"""

def hook(*, registry: Registry | None = None) -> Callable:
    """装饰 Hook 实现类 → 实例化并注册，返回原类。"""

def discover(packages: Iterable[str], *, registry: Registry | None = None) -> int:
    """importlib 动态导入每个包及其全部子模块（pkgutil.walk_packages），触发装饰器注册；
    返回本次新注册的条目总数。包名由调用方传入（框架不硬编码用户包名）。"""
```

### __init__.py
re-export：上述全部公开名 + `default_registry`。

## 行为契约

1. 框架运行期只从 Registry 取实例，**绝不 import 用户模块名**（代码强制：extension 包内不出现任何 `import app` / 硬编码 `"app."` 字符串；`discover` 的包名参数化，AGENTS.md §4、架构 §3.2 规则 2）。
2. 重复注册同 key 立即报错而非静默覆盖（代码强制）。
3. `@tool` 装饰后函数原样可调用（代码强制：返回原函数对象）。
4. `discover` 对同一包重复调用幂等（已导入模块不重复注册——依赖 importlib 模块缓存，重复注册会被规则 2 拦下，故 discover 内部对「已导入模块」跳过）。

## 边界契约

- Protocol 用 `typing.Protocol` + `runtime_checkable`，不提供基类实现（开发计划 D0-2 关键架构）。
- extension 只允许 import `lubanagent.core` + pydantic + 标准库；禁止 import sqlalchemy/fastapi/langchain*/app.*。
- 渠道层三扩展点只定义 Protocol 与契约小模型，不实现、不引用渠道层（AGENTS.md §3：核心不依赖渠道层）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/extension/` 全绿，覆盖：
   - 注册 mock ToolProvider 的 tool → `default_registry.get_tool` 取回（开发计划验收原文"注册一个 mock ToolProvider，能从 Registry 取到"）；
   - `@tool` 装饰函数 → `discover(["<tests 内伪造包>"])` 自动发现并注册；装饰后函数直接调用结果不变；
   - args_schema 正确反映参数名/类型/必填；risk/approval_required 进入 descriptor.contract；
   - 重复注册同 key 抛 `LubanError` 且 code=CONFIG_ERROR；`get_tool` 未注册抛 TOOL_NOT_FOUND；
   - `@graph`/`@hook`/auth/observability/knowledge 各注册-取回一轮；
   - 渠道三 Protocol：`isinstance` runtime_checkable 断言一个手写实现通过。
2. `grep -rn "import app\|from app" lubanagent/extension/` 无结果（领地隔离运行期兜底）。
3. `ruff check lubanagent/extension` 无错。

## 存疑项与决策

1. spec 未定义 `GraphDescriptor`/`Principal`/`AuthRequest`/`Snippet`/`SessionState`/`GuardVerdict` 的归属 → 决策：作为扩展点契约类型定义在 `protocols.py`（2026-07-23）。
2. 架构 §4.2 草图 `AuthProvider.verify(request: Request)` 暗示 fastapi 依赖 → 决策：框架中立 `AuthRequest`，D1-3 api 层适配（保持 extension 轻依赖）。
3. Hook 草图为同步 → 决策：async（运行期全 async）。
4. spec Registry 清单含 skill，但 SkillDescriptor 属 D3-3 → 决策：D0-2 不预设 skill 注册接口，D3-3 随 Skill 运行时补充（避免臆造错误契约）。
