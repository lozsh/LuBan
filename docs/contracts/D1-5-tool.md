# 契约 D1-5：`lubanagent/tool` Tool 治理

> 来源：开发计划 D1-5、技术方案 §5.4/§9（确定性规则代码执行）、架构设计 §3.3。
> 前置：D0-1（core：ToolDescriptor/RiskLevel/ToolContract/errors/TraceEvent）、D0-2（extension：Registry/RegisteredTool）。

## 范围

`lubanagent/tool/`：`executor.py`、`approval.py`、`mock.py`、`__init__.py` + `tests/lubanagent/tool/`。

## 接口契约

### executor.py
```python
@dataclass(frozen=True)
class ToolResult:
    success: bool
    value: Any = None                 # 成功时的返回值（须可 JSON 序列化；非序列化值转 str）
    error: dict[str, Any] | None = None   # LubanError.to_dict()["error"]
    duration_ms: float = 0.0
    attempts: int = 1

class ToolPermissionPolicy(Protocol):  # 确定性权限规则由代码执行（技术方案 §9）
    async def check(self, ctx: RunContext | None, tool: ToolDescriptor, args: dict[str, Any]) -> None:
        """拒绝 → 抛 LubanError(TOOL_PERMISSION_DENIED)。"""
# 默认实现 AllowAllToolPermissionPolicy（不拒绝）

ApprovalHandler = Callable[[ToolDescriptor, dict[str, Any]], Awaitable[bool]]  # True=批准

class ToolExecutor:
    def __init__(self, *, permission_policy: ToolPermissionPolicy | None = None,
                 approval_handler: ApprovalHandler | None = None,
                 clock: Callable[[], float] | None = None) -> None   # clock 可注入便于测试
    async def execute(self, registered: RegisteredTool, args: dict[str, Any],
                      *, ctx: RunContext | None = None) -> ToolResult: ...
    async def execute_by_key(self, key: str, args: dict[str, Any], *,
                             registry: Registry | None = None,
                             ctx: RunContext | None = None) -> ToolResult: ...
        # registry 缺省 default_registry；key 未注册 → ToolResult(success=False, error=TOOL_NOT_FOUND)
```

`execute` 管线（顺序即契约，全部代码强制）：
1. **参数校验**：按 descriptor.args_schema 用 Pydantic 校验/补默认值；失败 → `ToolResult(success=False, error=VALIDATION_ERROR)`（不进入执行）。
2. **权限检查**：`permission_policy.check`（无策略=放行）；拒绝 → error=TOOL_PERMISSION_DENIED。
3. **人工确认闸门**：`contract.approval_required=True`（或 `risk == RiskLevel.HIGH`）时——
   - 有 `approval_handler`：调用之；返回 False → error=TOOL_APPROVAL_REQUIRED（rejected）；
   - 无 handler → 直接 error=TOOL_APPROVAL_REQUIRED（由 D1-4 Runtime 接住暂停 Run）。
4. **执行**：handler 是协程函数 → await；同步函数 → `anyio.to_thread.run_sync` 卸载（代码强制，不阻塞事件循环）。
5. **超时**：`anyio.fail_after(contract.timeout_seconds)`；超时 → error=RUN_TIMEOUT（details 含 tool key 与 timeout_seconds）。
6. **重试**：仅当 `contract.idempotent=True` 且 `max_retries > 0` 时对**执行异常/超时**重试（指数退避 0.1s×2^n，可用注入 clock/sleep 控制）；**非幂等 Tool 永不重试**（代码强制，防重复副作用）。重试参数校验/权限/审批失败不重试。
7. handler 抛 `LubanError` → 透传其 code；抛其他异常 → error=TOOL_EXECUTION_FAILED（details 含异常类型名；消息不含堆栈）。

### approval.py（高风险人工确认，异步暂停/恢复）
```python
@dataclass(frozen=True)
class ApprovalRequest:
    id: str                 # uuid hex
    run_id: str
    tool_key: str
    args: dict[str, Any]
    created_at: datetime
    status: Literal["pending", "approved", "rejected", "expired"]

class ApprovalManager:
    """进程内审批管理器：发起 → 等待决定（asyncio.Future 暂停）→ decide 恢复。"""
    def __init__(self, *, default_timeout_seconds: float = 3600.0) -> None
    def create_request(self, run_id: str, tool: ToolDescriptor, args: dict[str, Any]) -> ApprovalRequest
    async def wait_for_decision(self, approval_id: str, *, timeout_seconds: float | None = None) -> bool
        # True=批准 / False=拒绝；超时 → 状态置 expired 并返回 False
    def decide(self, approval_id: str, approved: bool, *, decided_by: str = "") -> None
        # 不存在/已终结 → LubanError(VALIDATION_ERROR)
    def get(self, approval_id: str) -> ApprovalRequest | None
    def pending(self, *, run_id: str | None = None) -> list[ApprovalRequest]
    def as_handler(self, run_id: str) -> ApprovalHandler
        # 生成传给 ToolExecutor 的 handler：create_request + wait_for_decision
```

### mock.py（Tool 独立调试与 Mock，不起 Runtime）
```python
class MockToolHandler:
    """同签名词典驱动的假 handler：记录调用、返回罐头结果或抛罐头错误。"""
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None
    calls: list[dict[str, Any]]          # 每次调用的 args 快照
    async def __call__(self, **kwargs: Any) -> Any   # error 非空 → raise；否则返回 result

def make_mock_registered(descriptor: ToolDescriptor, *, result: Any = None,
                         error: Exception | None = None) -> RegisteredTool:
    """快速构造可注册/可执行的假 Tool（descriptor + MockToolHandler）。"""

async def debug_tool(registered: RegisteredTool, args: dict[str, Any]) -> ToolResult:
    """一行调试入口：等价 ToolExecutor 默认配置 execute（无权限策略、无审批）。"""
```

### __init__.py
re-export：ToolResult/ToolExecutor/ToolPermissionPolicy/AllowAllToolPermissionPolicy/ApprovalHandler/ApprovalRequest/ApprovalManager/MockToolHandler/make_mock_registered/debug_tool。

## 行为契约

1. Tool 可独立单测：不启动 Runtime/DB/网络即可执行一个 `@tool` 函数（代码强制，对应验收原文）。
2. 确定性规则（参数/权限/审批/超时/重试/幂等）全部代码执行，不依赖 Prompt（AGENTS.md §2）。
3. 审批暂停-恢复：`as_handler` 注入后，execute 在闸门处挂起直至 `decide`；批准后继续执行并返回结果（代码强制）。
4. 返回值 JSON 兜底：成功值 `json.dumps` 失败时转 `str(value)`（Trace/DB 落库前置）。

## 边界契约

- tool 包只 import `lubanagent.core`/`lubanagent.extension` + pydantic/anyio + 标准库；禁止 import persistence/runtime/api/langchain*/app.*。
- 不实现具体业务 Tool（架构 §3.3）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/tool/` 全绿：
   - 装饰一个 `@tool` 函数（用 D0-2 装饰器），`debug_tool` 不起 Runtime 执行成功；
   - 参数校验失败（缺必填/类型错）→ VALIDATION_ERROR 且 handler 未被调用；
   - 自定义 policy 拒绝 → TOOL_PERMISSION_DENIED；
   - approval_required 且无 handler → TOOL_APPROVAL_REQUIRED；`ApprovalManager.as_handler` 接线：execute 挂起 → 另一任务 `decide(True)` → execute 返回成功值（异步暂停/恢复实证）；
   - 超时：handler sleep 超过 timeout_seconds → RUN_TIMEOUT；
   - 重试：idempotent + max_retries=2 的 handler 前两次抛错第三次成功 → success=True 且 attempts=3；非幂等同场景 → 不重试（attempts=1）；
   - LubanError 透传 code；普通异常 → TOOL_EXECUTION_FAILED 且消息无堆栈；
   - execute_by_key 未注册 key → TOOL_NOT_FOUND；同步 handler 经线程卸载可并发（两个同步阻塞 execute 并发耗时 < 串行）。
2. `ruff check lubanagent/tool` 无错。

## 存疑项与决策

1. 审批持久化（跨进程恢复）spec 未要求 → 决策：进程内 ApprovalManager；跨进程恢复由 D1-4 结合 LangGraph interrupt 另行设计（2026-07-23）。
2. 重试策略 → 决策：仅幂等 Tool 可重试（防重复副作用），指数退避。
3. "权限检查"语义 → 决策：executor 内为可插拔 `ToolPermissionPolicy`（默认放行）；Agent↔Tool 绑定校验属 D1-4 Runtime 职责（agent.tools 清单核对）。
