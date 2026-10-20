# SSE 事件与错误码

> Run 的实时输出走 SSE（Server-Sent Events），错误走统一 `ErrorOut` 结构。事件名与错误码是框架对外契约，钉死在代码里。

## SSE 协议格式

`POST /v1/agents/{key}/runs` 返回 `text/event-stream`（响应头 `Cache-Control: no-cache`、`X-Accel-Buffering: no`）。每帧格式：

```
event: <事件名>
data: <JSON>

```

`data` JSON 结构为 `{"run_id": ..., "type": <内部事件类型>, **payload}`：

- `run_id`：本次 Run 的 ID，可用于回查 [Run 详情](./rest.md#run-runs) 与 Trace；
- `type`：内部 Trace 事件类型（如 `run_started`、`tool_call_completed`）；
- `payload`：事件载荷，外发前经脱敏（敏感字段值替换，args/结果/错误均处理）。

客户端示例：

```bash
curl -N -X POST http://localhost:8000/v1/agents/customer-service-bot/runs \
  -H "Content-Type: application/json" -d '{"input": "你好"}'
```

```
event: run.started
data: {"run_id": "r-abc123", "type": "run_started"}

event: token.delta
data: {"run_id": "r-abc123", "type": "model_token_delta", "text": "您好"}

event: run.completed
data: {"run_id": "r-abc123", "type": "run_completed", "end_reason": "final_answer", "output": "您好…"}
```

## 事件全表

只外发下表 15 种事件；payload 以实际推送为准（下表来自代码 emit 处核实）。

| 事件名 | 触发时机 | payload 要点 |
|---|---|---|
| `run.started` | Run 建档完成、开始执行 | 空（data 仅含 `run_id`/`type`） |
| `run.resumed` | 非终态 Run 从 checkpoint 续跑开始（`POST /v1/runs/{run_id}/resume`）；trace 连续，不重发 `run.started` | `resumed: true` |
| `token.delta` | 模型流式输出一个增量文本片段 | `text`（增量文本） |
| `tool.started` | 工具调用开始 | `tool`（工具 key）、`args`（已脱敏） |
| `tool.finished` | 工具调用成功 | `tool`、`result`（已脱敏）、`duration_ms` |
| `tool.failed` | 工具调用失败 | `tool`、`error`（错误 dict，已脱敏） |
| `run.completed` | Run 成功终态 | `end_reason`（如 `final_answer`/`knowledge_no_hit`）、`output`（最终回答） |
| `run.failed` | Run 失败终态 | `end_reason`、`error`（错误 dict） |
| `run.cancelled` | Run 被取消（含客户端 cancel） | `end_reason`（`cancelled`） |
| `tool.approval.requested` | 审批型工具触发人工确认 | `approval_id`、`tool`、`args`（已脱敏） |
| `tool.approval.resolved` | 审批被决定 | `approval_id`、`tool`、`approved`（bool）、`status`、`decided_by`、`decided_at` |
| `model.fallback` | 主模型能力不满足，降级到 fallback 槽位 | `from_profile`、`to_profile`、`model_key`、`reason` |
| `budget.warning` | 预算告警（当前场景：配置了费用上限但模型无单价，费用硬上限不可执行） | `reason`（如 `no_pricing`）、`model_key` |
| `budget.exceeded` | 预算硬上限命中，Run 转失败 | `error`（错误 dict） |
| `knowledge.no_hit` | 知识检索过滤后全 miss 且配了兜底话术，代码强制短路拒答 | `sources`、`score_threshold`、`hits_raw`、`hits_kept` |

注意：

- **内部粒度事件默认不外发**：`step_started`/`step_completed`/`model_call_started`/`model_call_completed` 等 Trace 粒度事件只落库（经 `GET /v1/traces/{run_id}` 可查），不进 SSE 流。
- `knowledge.no_hit` 触发时 Run 仍正常完成（`end_reason=knowledge_no_hit`），兜底话术即 `output`，未调模型、零 token 消耗。
- 审批等待期间 Run 状态为 `waiting_approval`，可通过 `POST /v1/approvals/{approval_id}/decide` 决定后恢复执行。
- **崩溃恢复（resume）**：普通 Agent Run 默认逐步落 checkpoint（`create_app` 缺省装配，`checkpoints` 表）；进程崩溃/中断后，非终态且有 checkpoint 的 Run 可调 `POST /v1/runs/{run_id}/resume` 续跑——预算以账本实耗量重建（不重复放水），模型版本沿用当时快照。边界：恢复是显式调用、无自动重启恢复；同一 Run 不能并发 resume（本进程内串行）；子 Run 归父编排、不可独立 resume；多 worker 部署下互斥保护仅限单进程。不想要 checkpoint 写入：`create_app(checkpointer=None)`。

## 错误码全表

错误码真实字符串值定义在 `lubanagent/core/errors.py` 的 `ErrorCode` 枚举；HTTP 状态映射在 `lubanagent/api/errors.py` 的 `ERROR_HTTP_STATUS`。

| 错误码 | HTTP 状态 | 含义 |
|---|---|---|
| `AGENT_NOT_FOUND` | 404 | Agent 不存在 |
| `AGENT_VERSION_NOT_FOUND` | 404 | Agent 版本快照不存在 |
| `RUN_NOT_FOUND` | 404 | Run / Trace 不存在 |
| `MODEL_NOT_FOUND` | 404 | 模型未注册 |
| `TOOL_NOT_FOUND` | 404 | 工具未注册 |
| `PROMPT_NOT_FOUND` | 404 | Prompt 不存在 |
| `PROMPT_VERSION_NOT_FOUND` | 404 | Prompt 版本不存在 |
| `KNOWLEDGE_SOURCE_NOT_FOUND` | 404 | 知识源不存在 |
| `GRAPH_NOT_FOUND` | 404 | 图未注册 |
| `AVATAR_NOT_FOUND` | 404 | Agent 形象未设置或文件缺失 |
| `EVAL_RESULT_NOT_FOUND` | 404 | 评测报告不存在 |
| `AUTH_FAILED` | 401 | API Key 缺失或不匹配 |
| `RATE_LIMITED` | 429 | 请求超出速率限制（`retryable=true`） |
| `TOOL_PERMISSION_DENIED` | 403 | 工具权限拒绝 |
| `AGENT_VERSION_UNRUNNABLE` | 422 | Agent 版本不可运行 |
| `VALIDATION_ERROR` | 422 | 请求参数校验失败 / 业务校验失败 |
| `CAPABILITY_MISMATCH` | 422 | 模型能力不满足 Agent 要求 |
| `NO_FALLBACK_AVAILABLE` | 422 | 无可用降级模型 |
| `BRAIN_NOT_MAPPED` | 422 | 大脑槽位未绑定模型 |
| `GRAPH_STRUCTURE_UNAVAILABLE` | 422 | 图 builder 非 DSL 构建器，无结构视图 |
| `CONFIG_ERROR` | 500 | 配置错误（如鉴权开启但未配 Key、管理器未装配） |
| `INTERNAL_ERROR` | 500 | 服务器内部错误（堆栈只进日志，不进响应） |

以下错误码是**运行期错误**，不在 HTTP 映射表中——它们出现在 `Run.error` 字段、`run.failed` SSE 事件的 `error` payload、以及 Trace 事件里，而不是作为 HTTP 响应：

| 错误码 | 含义 |
|---|---|
| `TOOL_APPROVAL_REQUIRED` | 审批型工具等待人工确认 |
| `TOOL_EXECUTION_FAILED` | 工具执行失败 |
| `BUDGET_EXCEEDED` | 预算硬上限命中 |
| `STEP_LIMIT_EXCEEDED` | 步数上限命中 |
| `RUN_TIMEOUT` | Run 超时 |
| `RUN_CANCELLED` | Run 被取消 |

未列入映射表的错误码默认映射 500。

## 错误响应体结构

统一结构 `ErrorOut`：`{"error": {code, message, details, retryable}}`。

```json
{
  "error": {
    "code": "AGENT_NOT_FOUND",
    "message": "Agent 不存在: no-such-bot",
    "details": {"agent_key": "no-such-bot"},
    "retryable": false
  }
}
```

三个路由层特例（以实际响应为准）：

- **409 冲突**：key/版本重复等资源冲突返回 HTTP 409，但 `code` 字段仍是 `VALIDATION_ERROR`（`conflict_response` 的路由级状态覆盖）。命中场景：Agent key 重复、Agent/Prompt 版本已存在、知识源重名。
- **422 校验错误**：FastAPI 请求校验失败返回 422，`details.errors` 为逐项错误数组：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数校验失败",
    "details": {
      "errors": [
        {"loc": ["body", "input"], "type": "missing", "msg": "Field required"}
      ]
    }
  }
}
```

- **404 + VALIDATION_ERROR**：审批决定接口对"审批不存在或已终结"返回 404，code 同样为 `VALIDATION_ERROR`。

相关页面：[REST API 参考](./rest.md)、[运行时与治理](../features/runtime.md)、[快速开始](../getting-started.md)。
