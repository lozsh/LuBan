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

## 异步工具调用——长任务不阻塞对话

**是什么**：工具可声明为异步执行（`execution_mode="async"`）。开关打开（环境变量 `TOOL_ASYNC_ENABLED=true`）后，异步工具被调用时立即返回 `{"status": "started", "task_id": "task-1"}`，任务在后台执行，完成后以 `task.finished` 事件推回本次 Run 的事件流。框架自动注入两个伴生工具：`task_result(task_id)` 取结果（任务还在跑时再调一次即继续等待）、`task_stop(task_id)` 终止任务。

**为什么需要**：评测跑批、批处理这类长任务如果同步阻塞，一次 Run 会被单个工具卡住很久。异步化后模型把长任务丢到后台就能继续对话与推理，前台响应与后台执行解耦。

**怎么用**：

```python
@tool(description="批量重导历史数据", execution_mode="async")
def batch_reimport(dataset: str) -> dict: ...
```

```bash
# 环境变量打开开关（默认关闭；关闭时 async 声明的工具按普通同步工具执行）
TOOL_ASYNC_ENABLED=true
```

**边界**：审批闸门仍在后台启动前同步完成——高风险工具不会借异步绕过人工确认；Run 结束时模型还没经 `task_result` 收走的任务被自动清理，并在终态事件 payload 的 `abandoned_tasks` 字段留痕；任务不持久化，进程重启后后台任务丢失，`task_result` 如实返回 `not_found`（不假装还在跑）。

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
