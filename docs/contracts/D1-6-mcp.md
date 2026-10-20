# 契约 D1-6：`lubanagent/mcp` MCP Client / Server

> 来源：开发计划 D1-6、技术方案 §5.4、用户手册 §4.2/§4.3、架构设计 §2/§3.3。
> 前置：D0-1（ToolDescriptor/ToolContract/errors）、D0-2（Registry）、D1-5（ToolExecutor 治理语义）、D1-1（McpServerRepository）。
> 依赖：`mcp` 官方 Python SDK（pyproject 已加 `mcp>=1.9`）。

## 范围

`lubanagent/mcp/`：`client.py`、`server.py`、`registry.py`、`__init__.py` + `tests/lubanagent/mcp/`。

## 契约决策

1. **测试零网络零子进程**：用 `mcp.shared.memory.create_connected_server_and_client_session`（官方 SDK 内存对）做端到端；stdio/HTTP 传输的真实进程联调记入 D1-9 手动清单。
2. **MCP 工具与本地 Tool 同治理**：远端工具封装为 `RegisteredTool`（descriptor + async handler），handler 内部调 MCP `call_tool`；治理（参数校验/权限/审批/超时/重试）由 ToolExecutor 统一执行——mcp 包不重复实现治理。
3. **server.py 用 FastMCP**（官方 SDK 高级 API），把 Registry 里的本地 Tool 暴露出去；工具输入 schema 直接用 descriptor.args_schema。

## 接口契约

### client.py
```python
@dataclass(frozen=True)
class McpServerConfig:
    key: str
    name: str = ""
    transport: Literal["stdio", "http"] = "stdio"
    command: str | None = None            # stdio：启动命令（如 "npx"）
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    url: str | None = None                # http：Streamable HTTP 端点
    timeout_seconds: float = 30.0

class McpClient:
    """接一个外部 MCP Server，枚举并把其工具封装为 RegisteredTool。"""
    def __init__(self, config: McpServerConfig) -> None
    async def list_tools(self) -> list[ToolDescriptor]
        # 远端 tool → ToolDescriptor(key=f"mcp:{config.key}:{tool.name}", name, description,
        #   args_schema=tool.inputSchema, risk=MEDIUM（远端默认中风险，可由 config 覆盖——决策 4）,
        #   contract=ToolContract(timeout_seconds=config.timeout_seconds),
        #   signature_hash=sha256(json(inputSchema))[:16])
    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any
        # 调远端 call_tool；远端错误 → LubanError(TOOL_EXECUTION_FAILED, details={"mcp_server": key, "tool": tool_name})
        # 返回值：文本内容拼接（多 content block 时 text 串联；非 text block 转 dict）
    def as_registered_tool(self, descriptor: ToolDescriptor) -> RegisteredTool
        # handler = async fn(**kwargs) -> await self.call_tool(remote_name, kwargs)
    async def close(self) -> None
# 传输：stdio → StdioServerParameters + stdio_client；http → streamablehttp_client（SDK 现成名）
```

### registry.py
```python
class McpRegistry:
    """MCP Server 登记处：config 来自 DB（mcp_servers 表）或直接注册。"""
    def __init__(self, *, repository: McpServerRepository | None = None) -> None
    def register(self, config: McpServerConfig) -> None          # key 重复 → CONFIG_ERROR
    async def load_from_db(self) -> int                          # 从 mcp_servers 表读 enabled 记录注册，返回条数（repository 为 None → 0）
    async def discover_tools(self, key: str, *, into: Registry | None = None) -> list[ToolDescriptor]
        # 建 McpClient → list_tools → as_registered_tool 注册进 extension Registry（缺省 default_registry）；
        # 远端不可达 → LubanError(CONFIG_ERROR, details={"mcp_server": key})，不静默
    def list_servers(self) -> list[McpServerConfig]
```

### server.py
```python
def create_mcp_server(*, registry: Registry | None = None, name: str = "lubanagent") -> FastMCP:
    """把 Registry 全部本地 Tool（key 不带 "mcp:" 前缀者）反向暴露为 MCP Server（用户手册 §4.3）。
    每个 RegisteredTool → FastMCP.add_tool(fn, name=key, description=...)；
    fn 签名由 args_schema 动态生成（**kwargs 入口，内部校验后调 handler 并返回结果）。"""
def run_server(*, transport: Literal["stdio", "http"] = "stdio", **kw) -> None
    # stdio → server.run()；http → server.run(transport="streamable-http")；供 CLI/进程入口
```

### __init__.py
re-export：McpServerConfig/McpClient/McpRegistry/create_mcp_server/run_server。

## 行为契约

1. 远端工具封装后与本地 Tool 同一张 Registry、同一套 ToolExecutor 治理（代码强制：经 `ToolExecutor.execute` 跑一个 MCP 封装 Tool，超时/参数校验生效）。
2. 自有 Tool 反向暴露后能被官方 client 调用（代码强制：内存对端到端 call_tool 返回一致结果）。
3. 远端不可达/调用失败 → 显式 LubanError（含 mcp_server key），不静默吞错。

## 边界契约

- mcp 包可 import core/extension/tool + mcp SDK + pydantic + 标准库；禁止 import runtime/api/model/observability/app.*（McpRegistry 的 repository 参数结构满足即可，不 import persistence 具体类——决策：类型标注用 Protocol 语义 `McpServerRepository | None`，允许 import persistence 仓储类作类型注解？不允许——用 `Any`/Protocol 保持边界，与 BrainMappingStore 同策：定义 `McpServerStore(Protocol)`）。
- MCP 治理不另起炉灶（权限/审批/超时/重试只在 ToolExecutor）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/mcp/` 全绿：
   - **反向暴露**：注册两个 `@tool`（一个加法、一个返回 dict）→ `create_mcp_server` → 内存对 client `list_tools` 可见 → `call_tool` 结果与直调一致；
   - **Client 封装**：对同一内存对 Server 用 `McpClient.list_tools` 取 descriptor（key 带 `mcp:<server>:` 前缀、args_schema 与远端一致）→ `as_registered_tool` 注册进 Registry → `ToolExecutor.execute` 调用成功；
   - **治理同权**：封装 Tool 的 timeout_seconds 生效（慢远端 → RUN_TIMEOUT）；参数校验失败 → VALIDATION_ERROR 不打到远端；
   - **错误显式**：远端工具内部抛错 → TOOL_EXECUTION_FAILED 含 mcp_server/tool details；
   - **McpRegistry**：dict 形态的 db 记录 → `load_from_db`（stub store）→ `discover_tools` 注册；重复 register → CONFIG_ERROR；不可达 server → CONFIG_ERROR。
2. 全量回归绿；`ruff check lubanagent/mcp` 无错。

## 存疑项与决策

1. 测试传输方式 → 契约决策 1（内存对）。
2. 远端工具默认风险级 → MEDIUM（config 可覆盖为 LOW/HIGH，McpServerConfig 加 `default_risk: RiskLevel = RiskLevel.MEDIUM`）。
3. 会话复用（长连 vs 每调一连）→ v1 每次 call 建立短会话（简单可靠）；长连复用与连接池记 TODO(D1-9 性能项)。
