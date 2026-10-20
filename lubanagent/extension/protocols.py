"""扩展点 Protocol: 用户实现接口而非继承基类, 含渠道层三扩展点契约。"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from lubanagent.core import Run, RunContext, RunStatus, Step, ToolDescriptor, TraceEvent


class GraphDescriptor(BaseModel):
    """图注册描述; builder 返回 LangGraph 编译图, extension 不 import langgraph。"""

    name: str
    description: str = ""
    builder: Callable[..., Any]


class Principal(BaseModel):
    """框架中立的鉴权主体。"""

    subject: str
    scopes: list[str] = []
    claims: dict[str, Any] = {}


class AuthRequest(BaseModel):
    """框架中立的鉴权请求, api 层负责从 fastapi Request 适配。"""

    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}


@runtime_checkable
class ToolProvider(Protocol):
    def tools(self) -> list[ToolDescriptor]: ...


@runtime_checkable
class GraphProvider(Protocol):
    def graphs(self) -> list[GraphDescriptor]: ...


@runtime_checkable
class AuthProvider(Protocol):
    async def verify(self, request: AuthRequest) -> Principal: ...
    # 失败抛 LubanError(ErrorCode.AUTH_FAILED)


@runtime_checkable
class TraceHandle(Protocol):
    """后端不透明句柄。"""


@runtime_checkable
class ObservabilityBackend(Protocol):
    @property
    def name(self) -> str: ...  # 后端标识, 注册表键
    def start_trace(self, run: Run) -> TraceHandle: ...
    def emit(self, handle: TraceHandle, event: TraceEvent) -> None: ...
    def end_trace(
        self, handle: TraceHandle, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None: ...  # summary 含 token/cost/end_reason 等汇总


@runtime_checkable
class Hook(Protocol):
    """运行期钩子, 全 async。"""

    async def pre_run(self, ctx: RunContext) -> None: ...
    async def pre_tool(
        self, ctx: RunContext, tool: ToolDescriptor, args: dict[str, Any]
    ) -> None: ...
    async def post_step(self, ctx: RunContext, step: Step) -> None: ...


class Snippet(BaseModel):
    """知识检索片段。"""

    content: str
    source: str
    score: float = 0.0
    metadata: dict[str, Any] = {}


@runtime_checkable
class KnowledgeConnector(Protocol):
    async def retrieve(self, query: str, top_k: int) -> list[Snippet]: ...


# ---- 渠道层三扩展点: 开源核心定义, 商用渠道层实现 ----
class SessionState(BaseModel):
    """会话状态机快照, state 取值由渠道层定义。"""

    session_key: str
    state: str
    data: dict[str, Any] = {}
    updated_at: datetime


@runtime_checkable
class SessionStateStore(Protocol):
    async def get(self, session_key: str) -> SessionState | None: ...
    async def save(self, state: SessionState) -> None: ...


class GuardVerdict(BaseModel):
    """出站风控判定; sanitized_text 为可选的改写后安全文本。"""

    allowed: bool
    reason: str = ""
    sanitized_text: str | None = None


@runtime_checkable
class OutputGuard(Protocol):
    async def check(self, text: str, *, context: dict[str, Any]) -> GuardVerdict: ...


@runtime_checkable
class ChannelConnector(Protocol):
    @property
    def name(self) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
