# REST API 参考

> 全部业务接口挂在 `/v1` 前缀下，统一鉴权与统一错误结构。本页是速查表；字段级细节以 `openapi.snapshot.json` 为准（前端 TypeScript 类型即由它生成）。

## 基本约定

- **Base URL**：`http://localhost:8000/v1`（本地默认端口；Docker 全栈同为 8000）
- **鉴权**：请求头 `Authorization: Bearer <key>`。`AUTH_ENABLED=false`（开发默认）时免鉴权走 anonymous；开启后所有 `/v1/*` 请求必须携带与 `API_KEY` 一致的 Bearer token，缺失或不匹配返回 401 `AUTH_FAILED`。开启鉴权却未配置 `API_KEY` 且无自定义 AuthProvider 时，服务启动期直接拒绝启动（fail fast）。详见[运行时与治理](../features/runtime.md)。
- **Content-Type**：JSON 接口为 `application/json`；文件上传（头像、知识文档、Skill 包）为 `multipart/form-data`
- **限流**：每主体每分钟默认 600 次（`RATE_LIMIT_PER_MINUTE`），超限返回 429 `RATE_LIMITED`
- **健康检查**：`GET /health`（不带 `/v1` 前缀，免鉴权），返回 `{"status": "ok"}`
- **OpenAPI 快照**：仓库根目录 `openapi.snapshot.json`，由 `/openapi.json` 导出冻结；工作台前端类型文件 `lubanagent/ui/src/api/schema.d.ts` 由它生成。接口字段以快照为准。

统一错误响应结构见 [SSE 事件与错误码](./sse-and-errors.md)。

## Agent 定义（agents）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/agents` | Agent 列表（当前版定义） | — | `AgentDefinition[]`（key/name/version/status/instructions/brain/tools/knowledge/memory/boundary…） |
| POST | `/v1/agents` | 创建 Agent | `AgentDefinition` 请求体（`key` 必填） | `AgentDefinition`（201）；key 重复 → 409 |
| GET | `/v1/agents/{key}` | 单个 Agent 当前定义 | — | `AgentDefinition`；不存在 → 404 `AGENT_NOT_FOUND` |
| POST | `/v1/agents/{key}/versions` | 追加不可变版本快照 | `AgentDefinition` 请求体（`version` 必填） | `AgentVersion`（201）；版本已存在 → 409 |
| GET | `/v1/agents/{key}/versions/{version}` | 读取指定版本快照 | — | `AgentVersion`（agent_key/version/definition/status/published_at） |
| PUT | `/v1/agents/{key}/avatar` | 上传虚拟形象（multipart `file`，svg/png/glb/gltf） | — | `AgentDefinition`（自动追加新版本） |
| GET | `/v1/agents/{key}/avatar` | 获取形象文件 | — | 文件流（带安全响应头）；未设置 → 404 `AVATAR_NOT_FOUND` |
| DELETE | `/v1/agents/{key}/avatar` | 移除形象（同样追加新版本） | — | `{removed: true}` |

Agent 定义字段与版本语义详见 [Agent 定义与版本](../features/agent-definition.md)。

```bash
# 创建版本快照
curl -X POST http://localhost:8000/v1/agents/my-bot/versions \
  -H "Content-Type: application/json" \
  -d '{"key": "my-bot", "name": "我的机器人", "version": 2, "instructions": "你是一个图书管理员"}'
```

## Run（runs）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| POST | `/v1/agents/{key}/runs` | 创建 Run，SSE 流式返回 | `input` 必填；可选 `session_id`/`user_id`/`version`/`variables`/`history`/`instructions_override` | SSE 事件流（见 [SSE 事件](./sse-and-errors.md)） |
| GET | `/v1/runs` | Run 列表 | query：`agent_key`/`session_id`/`user_id`/`limit`（默认 50，≤500） | `Run[]`（id/status/end_reason/token_usage/cost…） |
| GET | `/v1/runs/{run_id}` | Run 详情 | — | `Run`；不存在 → 404 `RUN_NOT_FOUND` |
| POST | `/v1/runs/{run_id}/cancel` | 取消运行中 Run | — | `{cancelled: bool}` |
| POST | `/v1/runs/{run_id}/resume` | 从最近 checkpoint 续跑非终态 Run（SSE 流式，帧格式同创建 Run） | — | SSE 事件流；Run/checkpoint 不存在 → 404 `RUN_NOT_FOUND`，已终态/本进程仍在活跃执行/子 Run → 422 `VALIDATION_ERROR`，关闭 checkpoint 时 → 500 `CONFIG_ERROR`（校验失败均在流开始前返回，不是 SSE 事件） |

```bash
# 发起对话（SSE 流式，-N 关闭缓冲）
curl -N -X POST http://localhost:8000/v1/agents/customer-service-bot/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "你好", "session_id": "s-1", "user_id": "debug"}'
```

```bash
# 查询某个会话的最近 Run
curl "http://localhost:8000/v1/runs?session_id=s-1&limit=10"
```

```bash
# 崩溃/中断后续跑一个非终态 Run（服务重启后也可用，前提：默认装配下 checkpoint 已逐步落库）
curl -N -X POST http://localhost:8000/v1/runs/<run_id>/resume
```

## 模型中心（models / brain-mappings）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/models` | 已注册模型列表（脱敏输出） | — | `ModelDefinitionOut[]`（key/provider/model_name/factory/capability/params/api_key_env/enabled；不含 base_url 等连接信息） |
| PUT | `/v1/models` | 注册/更新模型（upsert） | `key`/`provider`/`model_name`/`factory`/`api_key_env`；可选 `base_url`/`capability`/`params`（含单价字段） | 脱敏后的 `ModelDefinitionOut` |
| GET | `/v1/brain-mappings` | 四槽位当前映射（fast/smart/cheap/vision） | query：`env`（缺省取服务配置） | `BrainMappingOut[]`（env/profile/model_key） |
| PUT | `/v1/brain-mappings` | 绑定槽位到模型 | `profile`/`model_key`；可选 `env` | `BrainMappingOut`；模型不存在 → 404 `MODEL_NOT_FOUND` |

```bash
# 注册 OpenAI 兼容模型
curl -X PUT http://localhost:8000/v1/models -H "Content-Type: application/json" -d '{
  "key": "deepseek-chat", "provider": "deepseek", "model_name": "deepseek-chat",
  "factory": "openai_compat", "api_key_env": "DEEPSEEK_API_KEY",
  "base_url": "https://api.deepseek.com/v1",
  "capability": {"tool_calling": true},
  "params": {"input_price_per_1k": 0.002, "output_price_per_1k": 0.008}
}'
# 绑到 smart 槽位（切模型 = 改映射，不改代码不发版）
curl -X PUT http://localhost:8000/v1/brain-mappings -H "Content-Type: application/json" \
  -d '{"profile": "smart", "model_key": "deepseek-chat"}'
```

## Prompt 库（prompts）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/prompts` | Prompt 列表 | — | `PromptDefinition[]`（key/name/content/variables/version） |
| POST | `/v1/prompts` | 创建 Prompt | `PromptDefinition` 请求体 | `PromptDefinition`（201）；key 重复 → 409 |
| GET | `/v1/prompts/{key}` | 单个 Prompt 当前版 | — | `PromptDefinition` |
| GET | `/v1/prompts/{key}/versions` | 版本列表 | — | `PromptVersion[]` |
| POST | `/v1/prompts/{key}/versions` | 追加版本快照 | `PromptDefinition` 请求体（`version` 必填） | `PromptVersion`（201）；版本已存在 → 409 |
| GET | `/v1/prompts/{key}/versions/{version}` | 指定版本快照 | — | `PromptVersion`（prompt_key/version/content/variables） |

## 知识库（knowledge）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/knowledge/sources` | 知识源列表 | — | `KnowledgeSourceOut[]`（name/chunk_size/overlap/files/chunk_count/managed） |
| POST | `/v1/knowledge/sources` | 创建知识源并入库（multipart：`files` 多文件 + `name`/`description`/`chunk_size`/`overlap`） | `name` 满足 `^[a-z0-9][a-z0-9_-]{1,63}$` | `KnowledgeSourceOut`（201）；重名 → 409；单文件 ≤10MB、总量 ≤50MB、≤50 个文件 |
| DELETE | `/v1/knowledge/sources/{name}` | 删除知识源 | — | 204 |
| POST | `/v1/knowledge/sources/{name}/rechunk` | 重切块 | `chunk_size`（>0）/`overlap`（≥0） | `KnowledgeSourceOut` |
| GET | `/v1/knowledge/sources/{name}/chunks` | 查看切块 | query：`limit`（默认 200，≤1000） | `KnowledgeChunkOut[]`（chunk_index/content/metadata/created_at） |
| POST | `/v1/knowledge/query` | 检索测试 | `query` 必填；可选 `source`（缺省检索全部）、`top_k`（默认 5，≤50） | `KnowledgeHitOut[]`（source/chunk_index/content/score/metadata） |

```bash
# 上传知识文档（multipart）
curl -X POST http://localhost:8000/v1/knowledge/sources \
  -F "files=@faq.md" -F "files=@policy.pdf" \
  -F "name=lib-faq" -F "description=图书馆常见问题" \
  -F "chunk_size=500" -F "overlap=50"
```

## 工具与 Skill（tools / skills）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/tools` | 已注册工具清单 | — | `ToolDescriptor[]`（key/name/description/args_schema/risk/contract） |
| POST | `/v1/tools/{key}/test` | 在线测试工具（直执行，不经 Runtime） | 请求体即工具入参 JSON（可为 null） | `ToolResult`（success/value/duration_ms/error）；未注册 → 404 `TOOL_NOT_FOUND` |
| GET | `/v1/skills` | Skill 列表（内存视图） | — | `SkillOut[]`（key/name/version/manifest） |
| POST | `/v1/skills` | 上传 Skill zip 安装（multipart `file`，含 SKILL.md，≤10MB） | — | `SkillOut`（201） |
| DELETE | `/v1/skills/{key}` | 删除 Skill | — | 204；不存在 → 422 `VALIDATION_ERROR` |

```bash
# 在线测试工具
curl -X POST http://localhost:8000/v1/tools/query_order/test \
  -H "Content-Type: application/json" \
  -d '{"order_id": "A1001"}'
```

## Trace 与审批（traces / approvals）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/traces/{run_id}` | 按 Run 查 Trace 头 + 全量事件流 | — | `TraceOut`（trace + events[]）；不存在 → 404 `RUN_NOT_FOUND` |
| GET | `/v1/approvals` | 待审批列表 | query：`run_id`（可选过滤） | `ApprovalOut[]`（id/run_id/tool_key/args/status/created_at） |
| POST | `/v1/approvals/{approval_id}/decide` | 审批决定 | `approved`（bool）；`decided_by` 仅兼容旧请求体，归因取自鉴权主体 | `DecideOut`（status/decided_by/decided_at）；审批不存在或已终结 → 404 |

```bash
# 批准一次人工审批
curl -X POST http://localhost:8000/v1/approvals/ap-123/decide \
  -H "Content-Type: application/json" -d '{"approved": true}'
```

## 评测与图（evals / graphs）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/evals` | 评测报告摘要列表（由 `luban eval run` 落库，API 只读） | query：`dataset_key`/`agent_key`/`limit` | `EvalReportSummary[]` |
| GET | `/v1/evals/{result_id}` | 报告详情（逐 case pass/fail、断言明细、judge 结论、run_id） | — | `EvalReportDetail`；不存在 → 404 `EVAL_RESULT_NOT_FOUND` |
| GET | `/v1/graphs` | 已注册图列表（只读） | — | `GraphSummaryOut[]`（name/description/structure_available） |
| GET | `/v1/graphs/{name}` | 图拓扑结构详情（entry/nodes/edges/cond_edges） | — | `GraphDetailOut`；未注册 → 404 `GRAPH_NOT_FOUND`；非 DSL 构建器 → 422 `GRAPH_STRUCTURE_UNAVAILABLE` |

评测流程与报告解读见 [评测](../features/evals.md)；图的定义方式见 [运行时与治理](../features/runtime.md)。
