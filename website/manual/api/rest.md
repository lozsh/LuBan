# REST API 参考

> 全部业务接口挂在 `/v1` 前缀下，统一鉴权与统一错误结构。本页是速查表；字段级细节以 `openapi.snapshot.json` 为准（前端 TypeScript 类型即由它生成）。

## 基本约定

- **Base URL**：`http://localhost:8000/v1`（本地默认端口；Docker 全栈同为 8000）
- **鉴权**：请求头 `Authorization: Bearer <token-or-key>`。数据库账号登录返回的 bearer token 和配置的 `API_KEY` 都可使用；Master API Key 始终映射为 bootstrap admin。`AUTH_ENABLED=false` 是显式开发模式，走 anonymous 并拥有全部权限；生产必须保持 `true`。`POST /v1/auth/login` 免鉴权；其余 `/v1/*` 都要先通过身份与权限裁决。开启鉴权但无 `API_KEY`、无自定义 AuthProvider 且 `AUTH_DATABASE_ENABLED=false` 时，服务启动期直接拒绝启动。详见[登录与用户权限](../features/workbench.md)。
- **请求排查**：每个 HTTP 响应带 `X-Request-ID`；请求错误也返回该 ID，可用于 `GET /v1/logs?request_id=...`。不要把它当成 Run 的持久化字段。
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

## 模型中心（models / brain-profiles / brain-mappings）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/models` | 已注册模型列表（脱敏输出） | — | `ModelDefinitionOut[]`（key/provider/model_name/factory/capability/params/api_key_env/enabled；不含 base_url 等连接信息） |
| PUT | `/v1/models` | 注册/更新模型（upsert） | `key`/`provider`/`model_name`/`factory`/`api_key_env`；可选 `base_url`/`capability`/`params`（含单价字段） | 脱敏后的 `ModelDefinitionOut` |
| GET | `/v1/brain-mappings` | 活动槽位的实际环境映射，不枚举四项 | query：`env`（缺省取服务配置） | `BrainMappingOut[]`（env/profile/model_key），未映射不返回 |
| PUT | `/v1/brain-mappings` | 绑定动态槽位到模型，旧 alias 解析为当前 key | `profile`/`model_key`；可选 `env` | `BrainMappingOut`；模型不存在 → 404 `MODEL_NOT_FOUND`；槽位未知/已删除 → 404 `BRAIN_PROFILE_NOT_FOUND` |
| GET | `/v1/brain-profiles` | 活动槽位目录及服务端当前环境 | — | `{env,items}`；槽位 `key/name/description/aliases/reference_count/agent_count` |
| POST | `/v1/brain-profiles` | 新增槽位，可同时绑定模型 | `key`/`name`；可选 `description/model_key/env` | 201 槽位；标识或历史 alias 占用 → 409 `BRAIN_PROFILE_CONFLICT` |
| PATCH | `/v1/brain-profiles/{key}` | 编辑标识/名称/用途及可选映射，改名保留 alias 并移动所有环境映射 | 可选 `key/name/description/model_key/env` | 槽位；不存在 → 404；冲突 → 409 `BRAIN_PROFILE_CONFLICT` |
| GET | `/v1/brain-profiles/{key}/references` | 全部 Agent 历史版本的使用列表 | — | `{key,agent_count,references}`；Agent key/name、version、current、field |
| DELETE | `/v1/brain-profiles/{key}` | 无 Agent 引用才删除，移除所有环境映射 | — | `{removed:true}`；使用中 → 409 `BRAIN_PROFILE_IN_USE`，details 为 profile/references/agent_count，映射不变 |

目录标识匹配 `^[a-z][a-z0-9_-]{0,63}$`，名称长 1–128，用途说明 ≤512；非法请求返回 422。读目录/引用/映射需 `config:read`，写需 `config:write`。删除保护涵盖主槽位、降级槽位和摘要记忆隐含 cheap，不只当前版本；四个初始预设也遵守此规则。新增/编辑及可选映射在同一事务内，失败整体回滚。

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

## 登录与用户权限（auth / security）

| 方法 | 路径 | 说明 | 关键请求字段 | 关键响应字段 |
|---|---|---|---|---|
| POST | `/v1/auth/login` | 用户名密码登录；按来源主机限流 | `username`（1–64）、`password`（12–256） | `access_token`、`token_type:"bearer"`、`expires_at`、`user`；失败统一返回 401 语义，不区分“用户不存在”和“密码错误” |
| GET | `/v1/auth/me` | 当前账号、角色、权限与鉴权模式 | — | `subject/username/display_name/roles/permissions/auth_enabled/is_bootstrap` |
| POST | `/v1/auth/logout` | 撤销当前数据库会话 | — | `{logged_out:true}` |
| GET/POST | `/v1/security/users` | 用户列表/创建；需 `security:manage` | 创建：`username/display_name/password/role_keys/enabled`；密码 12–256 位 | `UserPublic[]` 或 `UserPublic`（200）；绝不返回 `password_hash` |
| PATCH | `/v1/security/users/{id}` | 改显示名、启用状态、角色或密码；需 `security:manage` | 可选 `display_name/enabled/role_keys/password` | `UserPublic`；禁用或改密会撤销该用户既有会话 |
| GET/POST | `/v1/security/roles` | 角色列表/创建；需 `security:manage` | `key/name/permissions` | `RolePublic[]` 或 `RolePublic`（200） |
| PATCH/DELETE | `/v1/security/roles/{key}` | 改权限或删除角色；需 `security:manage` | `name/permissions` | `RolePublic` 或 `{deleted:true}`；admin 受保护且必须拥有全部权限，仅 admin 不可删除（operator/auditor/viewer 可改可删），不能移除最后一个启用 admin |
| GET | `/v1/security/permissions` | 权限清单；需 `security:manage` | — | `[{key,name}]`，当前 10 项 |

```bash
curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-admin-password"}'
```

**权限映射**：`/v1/auth/*` 只要求已认证；`/v1/security/*` 要求 `security:manage`；`/v1/logs` 和 `/v1/diagnostics/*` 要求 `diagnostics:read`。GET runs/traces/approvals 要求 `run:read`，创建/续跑/工具测试/检索测试要求 `run:execute`，取消要求 `run:cancel`，审批决定要求 `run:approve`，评测读取与执行分别要求 `eval:read` / `eval:execute`；其他读取要求 `config:read`，未知写路径默认要求 `config:write`。权限不足返回 403 `PERMISSION_DENIED`。

**边界**：角色和权限服务于 LubanAgent 平台自身，不是通用企业后台；没有部门、租户或行级数据范围。每次请求从数据库重载启用用户和当前角色授权，禁用、改密、改角色或退出立即影响后续请求。

## 平台日志与诊断（logs / diagnostics）

| 方法 | 路径 | 说明 | 关键查询/请求字段 | 关键响应字段 |
|---|---|---|---|---|
| GET | `/v1/logs` | 查询已入库程序日志 | `session_id/run_id/trace_id/request_id/root_run_id/level/logger/q/from_time/to_time/limit/offset`；`limit` 默认 50、≤200 | `{items,total,limit,offset,dropped_count,write_failures}`；日志字段含关联 ID、`subject`、`exception`、`pathname`、`lineno`、`extra` |
| GET | `/v1/diagnostics/runs` | 分页诊断 Run | `session_id/run_id/trace_id/user_id/status/limit/offset`；`limit` 默认 50、≤200 | `{items:Run[],total,limit,offset}`；输入输出展示前脱敏 |
| GET | `/v1/diagnostics/runs/{run_or_trace_id}` | 按 Run ID 或 Trace ID 返回执行树 | — | `{run,runs,traces,log_count}`；`runs` 为根 Run 与全部后代，Trace 事件随每个 Run 返回 |

```bash
# 用登录返回的 token 查一次请求的日志；默认最多返回 50 条
curl "http://localhost:8000/v1/logs?request_id=<x-request-id>&limit=50" \
  -H "Authorization: Bearer <access_token>"
```

**边界**：写入是有界异步批次；查询前会尽量刷新队列，但队列满、数据库写入失败或进程崩溃仍可能丢日志。凭证类文本在入库前尽力脱敏，但不是任意 PII 识别；`logging` 集成不自动捕获所有 `print` 或第三方日志。`request_id` 仅是日志关联键，不写入 `runs` 表。
