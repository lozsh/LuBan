# 接入业务系统

> LubanAgent 的定位是引擎与基座，**用户端（App / 网页 / IM 机器人）由你的业务系统自建**——框架通过 REST / SSE 把能力完整开放出来，鉴权、用户体系、渠道都留在你这边。

## 调用模式：POST 发起 Run + 订阅 SSE 事件

一次运行 = 一个 HTTP 请求 + 一条 SSE 事件流：

```bash
curl -N -X POST http://localhost:8000/v1/agents/customer-service-bot/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "我上周的订单还没发货", "session_id": "sess-123", "user_id": "u-42"}'
# SSE 事件流：run.started → token.delta… → tool.started(query_order) → tool.finished → … → run.completed
```

事件名与字段的完整清单见 [SSE 事件与错误码](../api/sse-and-errors.md)。查历史 Run：

```bash
GET /v1/runs?agent_key=customer-service-bot&session_id=sess-123&user_id=u-42&limit=50
# agent_key / session_id / user_id / limit 均可选
```

## session_id / user_id 的语义与边界

- `session_id`——**会话归组**。同一会话的多轮 Run 用同一个 id 串联，Agent 开了会话/摘要记忆时按它读写历史；不传则每次 Run 独立。
- `user_id`——**用户排查标签**。业务系统把自己侧的用户 id 传进来，Run/Trace 落库时原样存下，之后可按用户过滤定位问题。

**边界（重要）**：`user_id` 只是透传的观测标签，引擎**不做用户管理、不做鉴权**——权限由你的业务网关负责。框架侧的 API Key 鉴权可插拔、可关，方便你接自己的网关。

## 审批回调怎么接

高风险工具（`risk="high"` 或 `approval_required=True`）执行前挂起等人工确认，你的业务系统这样接：

1. 订阅 SSE 事件流，收到 `tool.approval.requested` 事件（含审批单 id）；
2. 推给你的审批人（IM / 审批后台，由你实现）；
3. 调决定接口回写结果：

```bash
POST /v1/approvals/{approval_id}/decide
```

通过后流程继续，拒绝则按拒绝处理。审批记录持久化在 `approvals` 表：决定不随进程重启丢失、不重复（条件更新原子裁决）、可审计回查；`decided_by` 归因使用服务端验证的主体，不信任客户端填值。

## 可插拔扩展点清单

框架在关键位置留了 Protocol 扩展点，实现后经 Registry 注册即可替换默认行为（不碰框架代码）：

| 扩展点 | 默认实现 | 什么时候换 |
| --- | --- | --- |
| `AuthProvider` | API Key 鉴权（可关） | 接你自己的用户体系 / 网关注入的身份 |
| `RateLimiter` | 进程内限流 | 多实例部署换 Redis 等分布式实现 |
| `ObservabilityBackend` | Langfuse 自部署 | 切 LangSmith 或关闭（`OBSERVABILITY_BACKEND` env），见[换模型与换后端](./swap-backends.md) |
| `ChannelConnector` | 无内置实现 | **IM / 电商等消息渠道接入的扩展点**——核心不依赖它也能独立运行，业务系统走 REST/SSE 即可；配套 `SessionStateStore`（会话状态）、`OutputGuard`（出站审核）两个 Protocol |

## MCP 反向暴露：把自己的工具给别人

一键把 LubanAgent 里的自有 Tool（甚至整个 Agent）以标准 MCP Server 形式反向暴露，Claude Code、Cursor、其他 Agent 系统都能直接调用——企业的存量能力因此能被任何 MCP 生态客户端复用，不被锁在本系统内。

升级时的注意边界（详见 [UPGRADE 指南](./upgrade.md)）：`create_mcp_server()` 需显式传入 `export_tools`（仅列出要开放的工具 key），未指定时导出为空；MCP 工具调用经 `ToolExecutor` 的权限、风险与审批检查——高风险工具若没有审批器将被拒绝；HTTP MCP 入口须单独配置 `http_api_key`（不复用 REST API Key），stdio 仅用于受信本地调用。

## 边界

- 框架不做用户注册/登录/多租户——工作台内的聊天入口（/chat）是示例级入口，生产的用户端由业务系统自建走 REST/SSE。
- IM（微信/飞书/钉钉）与电商客服等渠道接入属商用渠道层模块，开源版不含；开源用户可经 `ChannelConnector` 扩展点自行接入。
- 会话/用户数据（`session_id`/`user_id`）在框架侧只作归组与观测标签，不构成身份体系。
