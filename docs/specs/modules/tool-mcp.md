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

# tool-mcp 模块

## 定位

`lubanagent/tool/` 与 `lubanagent/mcp/` 是框架的 Tool 执行治理层：确定性规则（参数校验、权限、审批、超时、重试、幂等）由代码强制执行而非 Prompt 声明（来源：`lubanagent/tool/executor.py:1`）。核心是 `ToolExecutor`（工具执行管线）、`ApprovalManager`（高风险人工确认，审批存储可插拔——缺省进程内 `MemoryApprovalStore`，注入 `DbApprovalStore` 后决定落库，见 `lubanagent/tool/approval.py:1-11`）、`McpClient`/`McpRegistry`/`create_mcp_server`（MCP 双向桥：接外部 MCP Server 的工具、把本地 Tool 反向暴露为 MCP Server）。本模块不落自有业务数据表，仅依赖 `persistence` 的 `mcp_servers` 表（`McpServerRow`，见 `lubanagent/persistence/models/misc.py:27-38`）持久化 MCP Server 连接配置。

## 接口契约

### HTTP

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/tools` | 列出 Registry 中全部已注册 Tool 的 `ToolDescriptor`（来源：`lubanagent/api/routes/tools.py:14-16`） | 无特殊错误，返回空列表 |
| POST | `/v1/tools/{key}/test` | 在线调试单个 Tool：`registry.get_tool(key)` + `debug_tool()` 直接执行，不经 Runtime（来源：`lubanagent/api/routes/tools.py:19-23`） | `key` 未注册 → `LubanError(TOOL_NOT_FOUND)` → 统一错误映射为 404（注释见同文件 21 行） |
| GET | `/v1/approvals` | 查询 pending 审批列表，可用 `run_id` 过滤（来源：`lubanagent/api/routes/approvals.py:31-36`，转调 `ApprovalManager.pending()`）；持久化装配下（`create_app` 默认注入 `DbApprovalStore`）列表跨进程重启仍可见 | 无 |
| POST | `/v1/approvals/{approval_id}/decide` | 人工决定审批（approved/rejected），决定人取已验证 `Principal.subject` 而非请求体 `decided_by`，返回 `DecideOut{status, decided_by, decided_at}`（`lubanagent/api/routes/approvals.py:39-56`）；decide 经 store 条件更新原子裁决，并发双 decide 恰一个成功 | 审批不存在或已非 `pending`（含已被并发决定）→ 路由手工返回 404 + `ErrorOut(VALIDATION_ERROR)`（同文件 46-50 行） |

路由前缀 `/v1` 统一在 `lubanagent/api/routes/__init__.py:17` 挂载，`tools.router`/`approvals.router` 分别在 21、23 行 `include_router`。

### RPC / MQ / Cron

无 MQ/Cron；MCP Server 可经受信本地 stdio 或受独立 Key 保护的 streamable HTTP 对外提供工具调用（接口和鉴权边界见下文 Python API 与边界规则）。

## 数据模型

### mcp_servers

对应实体：`McpServer`（`lubanagent/core/mcp.py:8-19`，pydantic 领域模型：`key`/`name`/`transport: Literal["stdio","http"]`/`config: dict`（连接细节 command/args/env/url/default_risk/timeout_seconds 收在子字段，经登记处 `_config_from_record` 展开为 `McpServerConfig`）/`enabled`），DB 行为 `McpServerRow`（`lubanagent/persistence/models/misc.py:27-38`），经 `McpServerRepository` 以领域对象读写（DB 列零改动）。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (INTEGER) | — | ✓ | 自增 | 主键 |
| key (String(128), unique) | key (str) | ✓ | — | Server 登记键，`McpRegistry.register()` 重复登记报 `CONFIG_ERROR`（`lubanagent/mcp/registry.py:33-39`） |
| name (String(256)) | name (str) | — | "" | 展示名 |
| transport (String(16)) | transport (Literal["stdio","http"]) | — | "stdio" | 连接方式 |
| config (JSON) | 展开为 command/args/env/url/timeout_seconds/default_risk 等 `McpServerConfig` 剩余字段（`lubanagent/mcp/registry.py:90-100` `_config_from_record`） | — | `{}` | 连接细节子字段，`default_risk` 若存在会被转换为 `RiskLevel` 枚举 |
| enabled (Boolean) | — | — | True | `load_from_db()` 只登记 `enabled=True` 的记录（`lubanagent/mcp/registry.py:47-49`） |
| created_at (DateTime tz) | — | — | `func.now()` | 创建时间 |

索引：
- `key` 唯一索引（`unique=True`，`lubanagent/persistence/models/misc.py:31`）— 防止同一 MCP Server 重复登记。

其余数据模型（`ToolDescriptor`/`ToolContract`/`RiskLevel`/`RegisteredTool`）定义于 `lubanagent/core/` 与 `lubanagent/extension/`，属于上游依赖，非本模块自有数据模型，详见"上下游"节。

### Python API 契约（本模块特殊性：以下为公开类/方法签名，非 DB 表）

**`ToolResult`**（`lubanagent/tool/executor.py:36-44`，`@dataclass(frozen=True)`）：一次工具执行的规范化结果。
- `success: bool`
- `value: Any = None`
- `error: dict[str, Any] | None = None` — 为 `LubanError.to_dict()["error"]` 结构
- `duration_ms: float = 0.0`
- `attempts: int = 1`

**`ToolExecutor`**（`lubanagent/tool/executor.py:107-261`）：
- `__init__(self, *, permission_policy: ToolPermissionPolicy | None = None, approval_handler: ApprovalHandler | None = None, clock: Callable[[], float] | None = None, sleep: Callable[[float], Awaitable[None]] | None = None) -> None`（110-121 行）
- `async def execute(self, registered: RegisteredTool, args: dict[str, Any], *, ctx: RunContext | None = None) -> ToolResult`（123-205 行）— 完整执行管线入口
- `async def execute_by_key(self, key: str, args: dict[str, Any], *, registry: Registry | None = None, ctx: RunContext | None = None) -> ToolResult`（207-220 行）— 按 key 从 Registry 查工具后执行；未注册返回 `ToolResult(success=False, error=...)` 而非抛异常
- `_validate_args` / `_invoke` / `_elapsed_ms` / `_fail` 为内部方法（222-261 行）

**`ToolPermissionPolicy`**（Protocol，`lubanagent/tool/executor.py:47-54`）：`async def check(self, ctx, tool, args) -> None`，拒绝时抛 `LubanError(TOOL_PERMISSION_DENIED)`。默认实现 `AllowAllToolPermissionPolicy`（57-63 行）不拒绝任何调用。

**`ApprovalHandler`** 类型别名（`lubanagent/tool/executor.py:24`）：`Callable[[ToolDescriptor, dict[str, Any]], Awaitable[bool]]`，`True` = 批准。

**`ApprovalRequest`**（`lubanagent/core/tool.py:40-64`，`@dataclass(frozen=True)`，`lubanagent.core` 导出，`tool` 兼容 re-export）——一次高风险工具人工确认请求，领域本体落 core 供 tool（治理）与 persistence（持久化）双向消费（两模块互不 import，均只依赖 core）：
- `id: str`、`run_id: str`、`tool_key: str`、`args: dict[str, Any]`、`created_at: datetime`
- `status: Literal["pending", "approved", "rejected", "expired"]`
- `decided_by: str | None = None`、`decided_at: datetime | None = None` — 终态归因与 UTC 时间
- `resolved(status, *, decided_by=None) -> ApprovalRequest`（57-64 行）— 返回终结态副本（`decided_at=now(UTC)`，frozen 语义原对象不变）

**`ApprovalStore`**（Protocol，`lubanagent/tool/approval.py:25-48`）：审批存储协议，全 async——`save(request) / get(approval_id) / list_pending(*, run_id) / list_all(*, run_id) / try_resolve(approval_id, status, *, decided_by)`；`try_resolve` 是 decide 竞态裁决点：原子 pending→terminal 条件更新，返回 `False` 表示不存在或已被并发决定。内置实现两个：`MemoryApprovalStore`（`approval.py:51-84`，进程内 dict 缺省实现，进程重启即丢、跨实例不可见）与 `DbApprovalStore`（persistence 层，见 persistence 模块规格；tool 不 import persistence，由 `app/factory.py` 装配注入）。

**`ApprovalManager`**（`lubanagent/tool/approval.py:87-195`）：
- `__init__(self, *, store: ApprovalStore | None = None, default_timeout_seconds: float = 3600.0, poll_interval_seconds: float = 2.0) -> None`（96-105 行）— `store=None` 缺省 `MemoryApprovalStore`（进程局部语义与旧版一致，跨实例 decide/wait 显式 `VALIDATION_ERROR` 不静默放行）
- `async def create_request(self, run_id: str, tool: ToolDescriptor, args: dict[str, Any]) -> ApprovalRequest`（108-120 行）— uuid4 hex id → `store.save` → 返回请求
- `async def wait_for_decision(self, approval_id: str, *, timeout_seconds: float | None = None) -> bool`（122-156 行）— 同进程 decide 经 `asyncio.Future` 即时唤醒；跨进程 decide 经周期轮询 store 兜底（每 `poll_interval_seconds` 重查一次，段超时用 `asyncio.shield` 保住 future）；总超时置 expired 返回 False
- `async def decide(self, approval_id: str, approved: bool, *, decided_by: str = "") -> ApprovalRequest`（158-176 行）— 经 `store.try_resolve` 条件更新原子裁决：并发/跨进程双 decide 恰一个成功，后到者抛 `LubanError(VALIDATION_ERROR)`；成功后返回终结态请求并唤醒本进程 waiter
- `async def get(self, approval_id: str) -> ApprovalRequest | None`（178-179 行）
- `async def pending(self, *, run_id: str | None = None) -> list[ApprovalRequest]`（181-182 行）
- `async def list_all(self, *, run_id: str | None = None) -> list[ApprovalRequest]`（184-186 行）— 全部记录含终态（审计/重启后回查入口）
- `as_handler(self, run_id: str) -> ApprovalHandler`（188-195 行）— 生成可直接传给 `ToolExecutor(approval_handler=...)` 的闭包（create_request + wait_for_decision）

**`MockToolHandler`**（`lubanagent/tool/mock.py:11-23`）：`__init__(self, *, result: Any = None, error: Exception | None = None)`；`async def __call__(self, **kwargs) -> Any` 记录调用于 `self.calls`。
**`make_mock_registered(descriptor, *, result=None, error=None) -> RegisteredTool`**（`lubanagent/tool/mock.py:26-32`）。
**`async def debug_tool(registered: RegisteredTool, args: dict[str, Any]) -> ToolResult`**（`lubanagent/tool/mock.py:35-37`）— 等价 `ToolExecutor()` 默认配置（无权限策略、无审批）直接 `execute`；被 `api/routes/tools.py` 的在线测试接口复用。

**`McpServerConfig`**（`lubanagent/mcp/client.py:20-32`，`@dataclass(frozen=True)`）：`key: str`、`name: str = ""`、`transport: Literal["stdio","http"] = "stdio"`、`command: str | None`、`args: list[str]`、`env: dict[str, str] | None`、`url: str | None`、`timeout_seconds: float = 30.0`、`default_risk: RiskLevel = RiskLevel.MEDIUM`。

**`McpClient`**（`lubanagent/mcp/client.py:57-277`，**长连复用**——首个调用触发建立会话并缓存，后续调用复用；此前"每次调用建短会话"模式已废弃）：
- `__init__(self, config: McpServerConfig, *, session_factory: SessionFactory | None = None) -> None`（74-80 行）— 内建 `asyncio.Lock`，同一 client 的调用串行化
- `config` 只读属性（82-84 行）
- `async def list_tools(self) -> list[ToolDescriptor]`（86-101 行）— 远端不可达抛 `LubanError(CONFIG_ERROR)`，`details` 含 `mcp_server`
- `async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any`（103-125 行）— 失败抛 `LubanError(TOOL_EXECUTION_FAILED)`；远端 `result.isError` 同样映射为 `TOOL_EXECUTION_FAILED`；调用抛非 `LubanError` 异常（连接断/超时/取消）时废弃连接，下次调用重建（自愈）
- `as_registered_tool(self, descriptor: ToolDescriptor) -> RegisteredTool`（127-137 行）— 生成的 handler 内部转调 `call_tool`
- `async def close(self) -> None`（139-142 行）— 显式释放长连（关闭子进程/HTTP 连接），幂等，供应用 shutdown 钩子调用
- 连接生命周期由专属 keeper 后台任务持有（`_Connection`/`_keep`，40-56、189-206 行）：MCP 传输 CM 基于 anyio，取消作用域绑定宿主任务、跨任务退出会破坏取消协议，故连接的建立/收尾全部收在 keeper 任务内，调用任务经 `Event` 请求建立/废弃——FastAPI 每请求一个任务的场景下复用仍安全

**`McpServerStore`**（Protocol，`lubanagent/mcp/registry.py:12-17`）：`async def list(self) -> list[McpServer]`（`McpServerRepository` 天然满足，测试可 stub）。

**`McpRegistry`**（`lubanagent/mcp/registry.py:20-120`）：
- `__init__(self, *, repository: McpServerStore | None = None, client_factory: Callable[[McpServerConfig], McpClient] | None = None) -> None`（27-36 行）
- `register(self, config: McpServerConfig) -> None`（38-45 行）— key 重复抛 `LubanError(CONFIG_ERROR)`
- `async def load_from_db(self) -> int`（47-57 行）— 无 repository 返回 0；只登记 `enabled` 记录
- `async def discover_tools(self, key: str, *, into: Registry | None = None) -> list[ToolDescriptor]`（59-85 行）— 枚举远端工具并注册进 `Registry`（缺省 `default_registry`）；McpClient 按 key 缓存，长连跨多次 discover 复用（`_client_for`，97-107 行）
- `list_servers(self) -> list[McpServerConfig]`（87-88 行）
- `async def aclose(self) -> None`（90-95 行）— 关闭全部缓存 client 的长连，幂等，供应用 shutdown 钩子调用（MCP 尚未接入 factory 装配，close/aclose 备好待接入时挂）

**`create_mcp_server(*, registry: Registry | None = None, name: str = "lubanagent", export_tools: Collection[str] | None = None, executor: ToolExecutor | None = None, ctx: RunContext | None = None, http_api_key: str | None = None, host: str = "127.0.0.1", port: int = 8000) -> FastMCP`**（`lubanagent/mcp/server.py:39-75`）— 缺省导出空集；仅精确白名单内且非 `mcp:` 的本地工具可枚举/调用。每次调用经 `executor`（缺省 `ToolExecutor()`）执行，可传 `ctx`；结果用 `structured_output=False` 文本内容返回，失败转 MCP `ToolError`。HTTP 鉴权由独立 `http_api_key` 驱动 SDK `AuthSettings`/`token_verifier`；未配置时直接构造的 HTTP ASGI app 也拒绝 token。
**`run_server(*, transport: Literal["stdio", "http"] = "stdio", **kw) -> None`**（`server.py:78-92`）— stdio 为受信本地入口；HTTP 无 `http_api_key` → `LubanError(CONFIG_ERROR)`，配 Key 用 streamable-http；未知传输 → `CONFIG_ERROR`。

## 行为

### 状态机

**审批请求状态机**（`ApprovalRequest.status`，`lubanagent/core/tool.py:53`）：

```
pending --(ApprovalManager.decide(approved=True))--> approved
pending --(ApprovalManager.decide(approved=False))--> rejected
pending --(wait_for_decision 超时 default_timeout_seconds)--> expired
```

触发接口：`POST /v1/approvals/{approval_id}/decide`（决定分支）；超时分支由 `wait_for_decision` 循环到期触发（`lubanagent/tool/approval.py:139-142`），不经 HTTP。终态流转经 `store.try_resolve` 的条件更新（`UPDATE ... WHERE status='pending'`）原子裁决，状态机只允许从 `pending` 单向流转一次：`decide()` 对已终结或不存在的 `approval_id` 抛 `LubanError(VALIDATION_ERROR)`（158-170 行），并发/跨进程双 decide 恰一个成功。批准/拒绝写入决定主体与时间；过期写入终态时间，`decided_by=None`，不伪称人工决定。`AutoApproveManager` 请求即批并标记 `decided_by="auto-approve"`（`approval.py:198-224`）；注入持久化 store 时自动批准同样落库。跨进程 decide 的感知延迟上限 ≈ `poll_interval_seconds`（默认 2s，可注入调小；同进程 decide 走 future 即时唤醒不受影响）。

### 调用树

#### ToolExecutor.execute（工具执行治理管线）

由 Runtime（Agent Loop）或 `debug_tool` 发起，对单次工具调用按固定顺序做参数校验→权限检查→人工确认→执行+超时+重试，任一前置环节失败立即返回失败 `ToolResult`，不进入执行（来源：`lubanagent/tool/executor.py:123-205` 注释"管线顺序即契约"）。

```
ToolExecutor.execute(registered, args, ctx)
├── 1. _validate_args  # 按 descriptor.args_schema 动态建 pydantic 模型校验; 失败 -> VALIDATION_ERROR, attempts=0
├── 2. [permission_policy 不为 None] permission_policy.check(ctx, descriptor, validated)
│   └── 拒绝 -> LubanError(TOOL_PERMISSION_DENIED), attempts=0
├── 3. [contract.approval_required or descriptor.risk == RiskLevel.HIGH] 人工确认闸门
│   ├── [approval_handler is None] -> ToolApprovalRequiredError, attempts=0 (由上层 Runtime 接住并暂停 Run)
│   └── [approval_handler 存在] await approval_handler(descriptor, validated)
│       ├── approved=True -> 放行进入第 4 步
│       └── approved=False -> LubanError(TOOL_APPROVAL_REQUIRED, decision=rejected), attempts=0
└── 4-6. 执行 + 超时 + 重试循环 (max_attempts = max_retries+1 若 idempotent 否则 1)
    ├── _invoke(handler, validated, timeout_seconds, ctx)  # anyio.fail_after 强制超时
    │   ├── [handler 签名含 _ctx] 注入 RunContext
    │   ├── [async handler] await handler(**args)
    │   └── [sync handler] anyio.to_thread.run_sync
    ├── 成功 -> ToolResult(success=True, value=_json_safe(value), attempts=attempt)
    ├── TimeoutError -> RUN_TIMEOUT
    ├── LubanError -> 原样透传
    ├── 其他 Exception -> 收敛为 TOOL_EXECUTION_FAILED（消息稳定为 `工具 X 执行失败: 类型名`，details 仅 `{"tool", "exception": 类型名}`，异常正文不进 details，`executor.py:192-201`）
    └── [attempt < max_attempts] sleep(0.1 * 2**(attempt-1)) 指数退避后重试; 否则返回失败 ToolResult
```

#### create_mcp_server（本地工具反向导出）

`export_tools` 缺省空集；遍历 Registry 仅注册白名单中的非 `mcp:` 工具。调用时 `_build_tool_fn` 将参数和可选 `RunContext` 交 `ToolExecutor.execute`，权限策略与审批处理器由注入的 executor 决定；无处理器时 HIGH 或 `approval_required` 拒绝，不执行 handler；失败映射 MCP `ToolError`（`mcp/server.py:63-75、95-113`；`tool/executor.py:123-205`）。不自动创建持久审批或 Trace sink。

#### McpRegistry.discover_tools（MCP 远端工具发现）

由运维/初始化流程或 API 发起，对已登记的 MCP Server 建立客户端、枚举远端工具、逐个注册进本地 `Registry`，使远端工具与本地 Tool 共享同一套 `ToolExecutor` 治理（来源：`lubanagent/mcp/registry.py:54-84` 注释）。

```
McpRegistry.discover_tools(key, into)
├── [key 未登记] -> LubanError(CONFIG_ERROR)
├── _client_for(config)  # 按 key 缓存 McpClient（长连跨多次 discover 复用；或 client_factory 注入的测试替身）
├── client.list_tools()
│   ├── 远端不可达 -> LubanError(CONFIG_ERROR, mcp_server=key)
│   └── 成功 -> list[ToolDescriptor] (key 前缀 "mcp:{server_key}:{tool_name}")
└── for each descriptor:
    ├── client.as_registered_tool(descriptor)  # handler 内部转调 call_tool
    └── registry.register_tool(descriptor, handler)  # 重复 key -> CONFIG_ERROR
```

## 边界规则

- 高风险判定：`contract.approval_required or descriptor.risk == RiskLevel.HIGH` 触发人工确认闸门，二者任一为真即拦截，非仅 `RiskLevel.HIGH` 单一条件（来源：`lubanagent/tool/executor.py:148`）。业务示例 `app/tools/customer_service/refund.py:20-27` 同时设置 `risk=RiskLevel.HIGH` 与 `approval_required=True`（冗余但一致）。
- 无 `approval_handler` 时人工确认环节直接返回 `ToolApprovalRequiredError` 失败结果（`attempts=0`），不会挂起等待；真正的"暂停 Run 等待人工决定"由上层 Runtime（Agent Loop）负责接住该错误并驱动 `ApprovalManager`（来源：`lubanagent/tool/executor.py:149-156` 注释 + `lubanagent/runtime/agent_loop.py:745-748` 中 `ToolExecutor(approval_handler=gate, ...)` 的桥接构造）。
- 重试仅对 `contract.idempotent == True` 的工具生效，`max_attempts = max_retries + 1`；非幂等工具固定 `max_attempts = 1`，即使抛可重试异常也不重试（来源：`lubanagent/tool/executor.py:170` 注释"非幂等工具永不重试"）。
- 重试退避策略固定为 `0.1 * 2 ** (attempt - 1)` 秒指数退避，无最大退避上限、无最大重试次数上限校验（由业务方在 `@tool(max_retries=...)` 自行设定合理值），代码位置 `lubanagent/tool/executor.py:203`。
- 超时由 `contract.timeout_seconds` 控制，经 `anyio.fail_after` 强制执行，同步 handler 通过 `anyio.to_thread.run_sync` 移交线程池执行但仍受同一超时约束（来源：`lubanagent/tool/executor.py:247-250`）。
- 参数校验先于权限检查、审批之前执行；校验失败（`VALIDATION_ERROR`）不消耗审批或执行资源（来源：管线顺序，`lubanagent/tool/executor.py:134-146`）。
- `_accepts_ctx` 依赖 handler 显式声明 `_ctx` 形参名才会注入 `RunContext`，这是一种"约定式扩展"而非类型驱动，普通工具函数无需感知（来源：`lubanagent/tool/executor.py:89-95` 注释）。
- **通用异常正文不进错误详情（H07）**：非 `LubanError` 异常收敛为 `TOOL_EXECUTION_FAILED` 时，消息为稳定格式 `工具 {key} 执行失败: {类型名}`、`details` 仅含 `{"tool": key, "exception": 类型名}`（`lubanagent/tool/executor.py:192-201`），不再拼 `str(exc)`——SDK/上游正文/URL/凭证不进 `ToolResult.error`（进而也不进 `TOOL_CALL_FAILED` 事件/`Run.error`）；完整堆栈仅进日志。
- MCP Server 默认无导出：`export_tools=None`/空集不注册任何工具；只精确匹配本地工具 key，并跳过 `mcp:` 远端工具，避免二次转发（`mcp/server.py:63-74`）。全部已导出调用均经 `ToolExecutor`，HIGH/`approval_required` 缺审批处理器时拒绝，不能把默认 executor 当成审批自动通过（`server.py:100-108`；`tool/executor.py:142-156`）。
- MCP HTTP 与 REST 鉴权互不代替：`run_server(transport="http")` 无独立 `http_api_key` 时启动报 `CONFIG_ERROR`；`create_mcp_server` 始终向 FastMCP 配置 SDK `AuthSettings`/`token_verifier`，故即使直接挂 `streamable_http_app()`，无 Key 的 token 也不通过；有 Key 时验证 Bearer token，MCP `client_id="mcp-key-holder"` 仅代表该 Key 持有者，不是独立人员身份（`mcp/server.py:27-62、78-92`）。默认绑定 `127.0.0.1`；stdio 仅面向受信本地进程，不代表 REST 主体身份。可注入 executor 与 `RunContext`，但无内建持久审批或 Trace sink 承诺。
- MCP 工具返回值经 `structured_output=False` 走文本 content（`mcp/server.py:69-74`），跨进程边界不传递结构化 Python 对象。
- MCP Client 长连复用（D1-9 性能项已落地）：首个调用触发建立会话（stdio 子进程 + initialize 握手，或 streamable-http 建连）并缓存，后续调用复用；调用抛非 `LubanError` 异常（连接断/超时/取消）时废弃连接、下次调用重建自愈（server 重启后不卡死坏连接）；`LubanError`（远端业务报错）不废弃连接（连接仍健康）。连接生命周期由专属 keeper 后台任务持有——anyio 取消作用域绑定宿主任务，跨任务退出会破坏取消协议（来源：`lubanagent/mcp/client.py:40-72` `_Connection`/`McpClient` docstring）。**同一 client 调用经 `asyncio.Lock` 串行化**（stdio 单管道/http 单会话，串行实现最简不引入请求交错）；需要真并行为同一 server 建多个 `McpClient` 实例。client 绑定首次使用的事件循环，跨多个 `asyncio.run` 使用同一实例不受支持。
- MCP 长连的释放：`McpClient.close()`/`McpRegistry.aclose()`（关闭全部缓存 client）幂等，供应用 shutdown 钩子调用；MCP 当前未接入 factory 装配（无对象可挂 shutdown 钩子），close/aclose 已备好待接入时调用（来源：`client.py:139-142`、`registry.py:90-95`）。
- 依赖方向：`lubanagent/tool/` 与 `lubanagent/mcp/` 均未 import 任何 `app/tools/*` 具体业务 Tool 实现（验证：对 `executor.py`/`approval.py`/`mock.py`/`client.py`/`server.py`/`registry.py` 的 import 语句逐一核对，只依赖 `lubanagent.core`、`lubanagent.extension`、`mcp`（第三方 SDK）、标准库与 `anyio`/`pydantic`），符合框架不依赖用户领地的架构约束（对照 `AGENTS.md` §4 领地隔离）；业务 Tool 通过 `@tool(...)` 装饰器（`lubanagent/extension/decorators.py:37-69`）反向注册进 `Registry`，方向为 `app → lubanagent`。
- 审批归因与持久化边界：HTTP 决定人由 `get_principal()` 给出的主体写入 `ApprovalRequest`，客户端提交的 `decided_by` 不采信（`api/routes/approvals.py:39-56`；`tool/approval.py:158-176`）。Runtime 的 `TOOL_APPROVAL_RESOLVED` Trace 带 `status`/`decided_by`/`decided_at`（ISO 8601 或 null，`runtime/agent_loop.py:1070-1085`）。审批记录经 `ApprovalStore` 可插拔：`create_app` 默认装配 `ApprovalManager(store=DbApprovalStore(db))` 落库（决定不随进程重启丢失、不重复、可审计回查）；`ApprovalManager()` 缺省仍为进程内 `MemoryApprovalStore`（库内嵌/测试零迁移）。**审批记录持久化 ≠ Run 自动恢复**——普通 Agent Run 已有逐步 checkpoint（2026-10 起），但恢复须显式调 `POST /v1/runs/{run_id}/resume`（非终态且有 checkpoint），无自动重启恢复、审批闸门语义不随 resume 改变，本机制只保证"决定本身"不丢、不重复、可归因。内置共享 REST Key 仅证明 Key 持有者，不能据此宣称具体人员归因或职责分离（`api/auth.py:21-30`）。自动批准标记 `auto-approve` 而非真人（`tool/approval.py:216-218`）。

## 名词

| 术语 | 语义 |
|---|---|
| RiskLevel | 工具风险等级枚举：`low`/`medium`/`high`（`lubanagent/core/tool.py:9-14`），`high` 会与 `approval_required` 一起触发人工确认闸门 |
| ToolContract | 工具执行契约（超时/重试/幂等/是否需审批），由代码强制执行而非 Prompt 声明（`lubanagent/core/tool.py:17-23`），本模块只消费不定义 |
| ToolDescriptor | 工具注册描述（key/name/description/args_schema/risk/contract/signature_hash），`lubanagent/core/tool.py:26-35`，本模块只消费不定义 |
| RegisteredTool | `descriptor + handler` 的注册态组合（`lubanagent/extension/registry.py:18-21`），是 `ToolExecutor.execute` 的第一个入参类型 |
| Registry / default_registry | 用户扩展运行期注册表，框架侧只读查询，不 import 用户模块（`lubanagent/extension/registry.py:24-118`） |
| Approval（审批） | 高风险工具执行前的人工确认动作，由 `ApprovalManager` 管理生命周期（`lubanagent/tool/approval.py`），是"确定性规则由代码执行"原则的一部分；记录存储经 `ApprovalStore` 协议可插拔（进程内缺省 / `DbApprovalStore` 落库） |
| MCP Server（本模块视角有两义） | ① 外部工具提供方，`McpClient` 连接它、`McpRegistry` 登记它（`lubanagent/mcp/client.py`、`registry.py`）；② `create_mcp_server` 把本地 Tool 反向暴露成的服务端（`lubanagent/mcp/server.py`），二者是同一协议的 Client/Server 两端，命名相同需按上下文区分 |
| signature_hash | `ToolDescriptor` 的签名摘要字段，用于版本兼容治理（`lubanagent/core/tool.py:27` 注释），MCP 远端工具的 `signature_hash` 由 `args_schema` JSON 序列化后 sha256 前 16 位生成（`lubanagent/mcp/client.py:122-124`） |
| debug_tool | 跳过 Runtime、跳过权限/审批的一行式工具调试入口，等价 `ToolExecutor()` 默认配置执行（`lubanagent/tool/mock.py:35-37`），被 `/v1/tools/{key}/test` 复用 |

## 上下游

**上游依赖**（本模块消费外部数据/类型）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent/core`（core 模块） | Python import | `ToolDescriptor`、`ToolContract`、`RiskLevel`、`RunContext`、`ErrorCode`、`LubanError`、`ToolApprovalRequiredError`、`ApprovalRequest`（`lubanagent/tool/executor.py:14-21`、`lubanagent/tool/approval.py:18`、`lubanagent/mcp/client.py:15`；`ApprovalRequest` 定义本体在 `core/tool.py:40-64`） |
| `lubanagent/extension`（extension 模块） | Python import | `RegisteredTool`、`Registry`、`default_registry`（`lubanagent/tool/executor.py:22`、`lubanagent/tool/mock.py:6`、`lubanagent/mcp/client.py:16`、`lubanagent/mcp/registry.py:7`、`lubanagent/mcp/server.py:10`）；业务 Tool 经 `extension.tool` 装饰器（`lubanagent/extension/decorators.py:37-69`）注册进 `Registry`，本模块的 `ToolExecutor`/`McpRegistry` 再从中读取 |
| `lubanagent/persistence`（persistence 模块） | `McpServerStore.list()` 协议 | `mcp_servers` 表记录（`McpServerRow`，`lubanagent/persistence/models/misc.py:27-38`），经实现该 Protocol 的 Repository 注入 `McpRegistry(repository=...)` 供 `load_from_db()` 读取（`lubanagent/mcp/registry.py:12-17, 42-52`） |
| 第三方 `mcp` SDK | Python import | 客户端 `ClientSession`、`stdio_client`、`streamablehttp_client` 等；服务端 `FastMCP`、`AuthSettings`、`AccessToken`、`ToolError`（`lubanagent/mcp/server.py:8-11`） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent/runtime`（Agent Loop） | Python import | `runtime/agent_loop.py` 直接构造 `ToolExecutor`、`ApprovalManager`，并在人工确认场景动态构造带 `approval_handler=gate` 的新 `ToolExecutor`（`lubanagent/runtime/agent_loop.py:207, 230, 899-944`），是本模块最主要的消费方，实现"暂停 Run 等待审批、审批后恢复"的编排 |
| `lubanagent/api`（API 层） | Python import | `api/routes/tools.py` 消费 `ToolResult`/`debug_tool`；`api/routes/approvals.py` 消费 `ApprovalRequest`；`api/deps.py` 提供 `ApprovalManagerDep` 依赖注入（`lubanagent/api/deps.py:12`） |
| `lubanagent/app`（应用工厂） | Python import | `app/factory.py` 引入 `ApprovalManager` 并注入 `DbApprovalStore(db)` 构建应用级单例（`lubanagent/app/factory.py:32, 99`）——审批决定经 persistence 落库，跨进程可见可恢复 |
| 外部 MCP Client（如 Claude Desktop 等） | 受信本地 stdio / 独立 Key 的 streamable-http | `create_mcp_server(export_tools=...)` 仅导出精确白名单本地工具并走 `ToolExecutor`；HTTP 无 `http_api_key` 拒绝启动/鉴权失败关闭，REST Key 不复用（`lubanagent/mcp/server.py:39-108`） |
| 外部 MCP Server（第三方工具服务） | stdio / streamable-http（经 `mcp` SDK） | `McpClient.list_tools()`/`call_tool()` 长连复用方式连接并调用外部 Server（`lubanagent/mcp/client.py:86-125`） |
