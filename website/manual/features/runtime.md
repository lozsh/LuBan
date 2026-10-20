# Agent Runtime

> 本页讲执行引擎的 Run 循环、三道硬边界、`session_id` / `user_id` 两个字段的语义，以及 SSE 流式事件的订阅方式。

**是什么**：执行引擎。跑"模型 ↔ 工具"循环：模型决定调工具 → 执行 → 结果回填 → 再推理，直到给出最终答复或触碰边界。每次执行是一个 **Run**，受步数/预算/超时三道硬边界约束，支持取消；全程输出统一的 **SSE 流式事件**（token 增量、工具调用开始/结束、Run 状态），前端或业务系统订阅即可。

**为什么需要**：这是"每个团队都在重写"的第一大件；安全边界（最大步数、预算、超时）和[成本治理](./cost.md)的熔断都挂在 Runtime 上由代码强制，不是挂在 Prompt 里靠模型自觉。

**怎么用**（示意）：

```bash
curl -N -X POST http://localhost:8000/v1/agents/customer-service-bot/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "我上周的订单还没发货", "session_id": "sess-123", "user_id": "u-42"}'
# SSE 事件流：run.started → token.delta… → tool.started(query_order) → tool.finished → … → run.completed
```

请求体两个常用可选字段：
- `session_id`——**会话归组**。同一会话的多轮 Run 用同一个 id 串联，Agent 开了会话/摘要记忆时按它读写历史；不传则每次 Run 独立。
- `user_id`——**用户排查标签**。业务系统把自己侧的用户 id 传进来，Run/Trace 落库时原样存下，之后 `GET /v1/runs?user_id=...` 可按用户过滤定位问题。注意它只是透传的观测标签，引擎**不做用户管理、不做鉴权**——权限由你的业务网关负责。

查历史 Run：`GET /v1/runs?agent_key=customer-service-bot&session_id=sess-123&user_id=u-42&limit=50`（`agent_key`/`session_id`/`user_id`/`limit` 均可选）。

**崩溃恢复（resume）**：普通 Run 默认在每个执行节点边界落 LangGraph checkpoint（`checkpoints` 表，零配置启用）。进程崩溃或中断后，非终态且有 checkpoint 的 Run 可显式续跑：

```bash
curl -N -X POST http://localhost:8000/v1/runs/<run_id>/resume
# SSE 帧格式与创建 Run 一致，首个事件为 run.resumed（trace 连续，不重发 run.started）
```

续跑预算按已消耗账本重建（不会因重启重新放水），模型版本沿用当时快照。边界：恢复是显式调用，没有自动重启恢复；同一 Run 不能并发 resume（本进程内串行，多 worker 部署勿双 resume）；子 Run 归父编排、不可独立 resume；不想要 checkpoint 写入可 `create_app(checkpointer=None)` 关闭。

SSE 事件协议与错误码全表见 [SSE 事件与错误码](../api/sse-and-errors.md)。

**边界**：Runtime 只管单 Agent 的循环；跨节点、有状态、可暂停的复杂流程用[图编排](./graphs.md)。
