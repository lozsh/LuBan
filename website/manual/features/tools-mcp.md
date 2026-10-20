# Tool 与 MCP

> 本页讲业务工具（Python Tool）、MCP 官方定义、MCP Client 接入外部工具生态、MCP Server 反向暴露自有工具，以及工具治理链路。

## Python Tool——业务动作唯一要写代码的地方

**是什么**：Tool 就是一段 Python 函数，加三样声明：自然语言描述（给模型看）、参数 JSON Schema（给模型填）、治理属性（权限、是否高风险、超时/重试/幂等）。

**为什么需要**：确定性规则（权限、金额、审批）必须由代码执行，不能靠 Prompt 声明。Tool 支持独立调试与 Mock——不启动 Agent 也能单测一个 Tool。

```python
@tool(description="查询订单状态与物流", risk="low", idempotent=True, timeout_seconds=10)
def query_order(order_id: str) -> dict: ...
```

写 Tool 的完整步骤见[改造指南](../customization/overview.md)。

## MCP 是什么（官方定义）

MCP（Model Context Protocol）是 Anthropic 发起并开源的**开放标准**，官方定义："连接 AI 应用与外部系统的开源标准……可以把它想象成 AI 应用的 USB-C 接口"（[modelcontextprotocol.io](https://modelcontextprotocol.io/docs/getting-started/intro)）。一个 MCP Server 可以暴露三类东西：**Tools**（可执行动作）、**Resources**（数据）、**Prompts**（提示模板）；传输支持 stdio 与 Streamable HTTP。

生态事实（2026-07 核查）：官方组织 [github.com/modelcontextprotocol](https://github.com/modelcontextprotocol) 下 `servers` 仓库 88.6k★、`python-sdk` 23.6k★；AWS 官方也下场出了 [awslabs/mcp](https://github.com/awslabs/mcp)（9.5k★，"Open source MCP Servers for AWS"）。MCP 已是事实标准。

## MCP Client——接入别人的工具生态

在配置里登记一个外部 MCP Server，它暴露的工具经过封装后自动进入 Agent 的可用 Tool 清单，与本地 Python Tool 走同一套治理（权限检查、人工确认、超时重试）：

```yaml
mcp_servers:
  github-mcp: { transport: "http", url: "https://...", env: { GITHUB_TOKEN: "<token>" } }
  fs-mcp:     { transport: "stdio", command: "npx -y @modelcontextprotocol/server-filesystem /data" }
```

## MCP Server 暴露——把自己的工具给别人

一键把 LubanAgent 里的自有 Tool（甚至整个 Agent）以标准 MCP Server 形式反向暴露，Claude Code、Cursor、其他 Agent 系统都能直接调用。企业的存量能力因此能被任何 MCP 生态客户端复用，不被锁在本系统内。

## 治理

调用前权限与参数检查 → 高风险 Tool 强制人工确认 → 执行（超时/重试/幂等策略）→ 全程进 Trace（见[运行观测](./observability.md)）。
