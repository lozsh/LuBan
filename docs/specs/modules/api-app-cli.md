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

# api-app-cli 模块

## 定位

本模块是 LubanAgent 的统一 HTTP 接入层 + 应用装配根 + 命令行入口，由三个紧密关联的子包组成：`lubanagent/api/`（REST/SSE 路由、鉴权、依赖注入、错误映射、请求/响应模型）、`lubanagent/app/`（`create_app()` 装配工厂 + lifespan 钩子）、`lubanagent/cli/`（`luban` 命令行：init/export/migrate/eval）。本模块不持有自己的业务实体或 ORM 表，是其余所有能力层（runtime/persistence/tool/skill/observability/extension/model）对外暴露的统一入口（来源：`lubanagent/api/__init__.py:1`「api：REST/SSE 接入层」、`lubanagent/app/factory.py:1`「应用装配根」、`lubanagent/cli/main.py:1`「luban 命令行」）。

## 接口契约

### HTTP

框架 `/v1/*` 路由由 `api_router` 的 `Depends(get_principal)` 保护；工厂 `extra_routers` 注册时也注入该依赖，用户领地团队 router 自身另显式绑定身份依赖（`lubanagent/app/factory.py:154-156`、`app/api/teams.py:12-16`）。鉴权关闭时按配置放行，但仍限流；`/health` 不鉴权（`factory.py:150-156`）。未知 `/v1/*` 是 JSON 404，不回退 SPA。

#### agents.py（`lubanagent/api/routes/agents.py`）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/agents` | 列出全部 Agent 定义 | 无特殊错误（空库返回空列表） |
| POST | `/v1/agents` | 创建 Agent（201），请求体 `AgentDefinition`；落库前先经 `resolve_prompt_reference` 解析 Prompt 库引用（`prompt_key` 非空时把 Prompt 内容写入 `instructions` 落进快照，见下方 prompts.py 节；`prompt_key` 为 `None` 走纯内嵌存量路径，行为不变） | key 已存在 → 409 `conflict_response`（VALIDATION_ERROR 语义）；引用的 prompt 不存在 → 404 `PROMPT_NOT_FOUND`；钉住的 prompt 版本不存在 → 404 `PROMPT_VERSION_NOT_FOUND`（来源：`agents.py:28`） |
| GET | `/v1/agents/{key}` | 查询单个 Agent 当前定义 | key 不存在 → `LubanError(AGENT_NOT_FOUND)` → 404 |
| POST | `/v1/agents/{key}/versions` | 追加不可变版本快照（201），请求体 `AgentDefinition`；落库前同样经 `resolve_prompt_reference` 解析（来源：`agents.py:59`）——**整份定义回传（含旧 `prompt_version`）会钉住旧版 Prompt 内容**，要吃 Prompt 当前版须显式传 `prompt_version: null` | key 不存在 → 404 `AGENT_NOT_FOUND`；该 version 已存在 → 409 `conflict_response`（版本不可覆盖）；prompt/版本不存在 → 404 `PROMPT_NOT_FOUND`/`PROMPT_VERSION_NOT_FOUND` |
| GET | `/v1/agents/{key}/versions/{version}` | 查询指定版本快照 | 版本不存在时区分：Agent 本身不存在 → 404 `AGENT_NOT_FOUND`；Agent 存在但该版本不存在 → 404 `AGENT_VERSION_NOT_FOUND`（来源：`agents.py:61-76` 二次查询判定） |
| PUT | `/v1/agents/{key}/avatar` | 上传虚拟形象（multipart 字段 `file`，svg/png/glb/gltf，≤ 20MB）；**上传即版本**——服务端复制当前定义设 `avatar` 字段、追加新版本（照 Prompt 分配先例），返回新 `AgentDefinition`；文件落盘 `avatar_files_root/{key}/avatar.{ext}`（换类型自动清旧扩展名文件） | Agent 不存在 → 404 `AGENT_NOT_FOUND`；坏后缀/超限 → 422 `VALIDATION_ERROR`；路由按 64 KiB 块读取，超过 20 MiB 立即停止且不调用 AvatarManager.save（`routes/avatars.py:23-24、64-85`） |
| GET | `/v1/agents/{key}/avatar` | 取当前形象文件（`FileResponse`，Content-Type 按后缀：image/svg+xml / image/png / model/gltf-binary / model/gltf+json）；响应带 `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'` + `X-Content-Type-Options: nosniff`（用户上传 SVG 直开也不执行脚本）。本模块首个二进制响应端点 | Agent 不存在 → 404 `AGENT_NOT_FOUND`；未设置形象或文件缺失 → 404 `AVATAR_NOT_FOUND` |
| DELETE | `/v1/agents/{key}/avatar` | 移除形象：删文件目录 + 追加 `avatar: None` 新版本 → `{removed: true}` | Agent 不存在 → 404 `AGENT_NOT_FOUND`；无形象 → 404 `AVATAR_NOT_FOUND` |

#### prompts.py（`lubanagent/api/routes/prompts.py`）—— Prompt 库，与 agents 路由同型

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/prompts` | 列出全部 Prompt（当前版视图） | 无特殊错误（空库返回空列表） |
| POST | `/v1/prompts` | 创建 Prompt（201），请求体 `PromptDefinition`，落 `prompts` 行 + 首个 `prompt_versions` 行 | key 已存在 → 409 `conflict_response`（key 重复） |
| GET | `/v1/prompts/{key}` | 查询单个 Prompt 当前版定义 | key 不存在 → `LubanError(PROMPT_NOT_FOUND)` → 404 |
| GET | `/v1/prompts/{key}/versions` | 列出版本快照（`version` 降序） | key 不存在 → 404 `PROMPT_NOT_FOUND` |
| POST | `/v1/prompts/{key}/versions` | 追加不可变版本快照（201），请求体 `PromptDefinition`，推进 `current_version` | key 不存在 → 404 `PROMPT_NOT_FOUND`；该 version 已存在 → 409 `conflict_response`（版本不可覆盖） |
| GET | `/v1/prompts/{key}/versions/{version}` | 查询指定版本快照 | 版本不存在时区分：Prompt 本身不存在 → 404 `PROMPT_NOT_FOUND`；Prompt 存在但该版本不存在 → 404 `PROMPT_VERSION_NOT_FOUND`（来源：`prompts.py:70-86` 二次查询判定，与 agents 版本详情同模式） |

#### runs.py（`lubanagent/api/routes/runs.py`）—— 含唯一 SSE 流式接口

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| POST | `/v1/agents/{key}/runs` | **SSE 流式**创建 Run；请求体 `RunCreateRequest`；返回 `StreamingResponse(media_type="text/event-stream")`，headers `{Cache-Control: no-cache, X-Accel-Buffering: no}`（来源：`runs.py:17,62-64`）。实现细节：先 `await anext(stream)` 预热首事件，把"定义加载/可运行性失败"这类启动期同步错误（404/422）在流真正开始前抛出，走统一错误映射而非塞进 SSE 事件流（来源：`runs.py:48-54` 注释） | 启动期失败（Agent/版本不存在、版本不可运行等）在流开始前抛出，仍走 `LubanError` → 统一错误映射；流中途失败则以 `RUN_FAILED`/`RUN_CANCELLED` SSE 事件呈现，不是 HTTP 错误 |
| GET | `/v1/runs` | 列 Run，query：`agent_key`、`session_id`、`user_id`（透传观测标签过滤，无用户管理语义）、`limit`（1-500，默认 50，`Query(ge=1, le=500)`） | 无特殊错误 |
| GET | `/v1/runs/{run_id}` | 查询单条 Run | 不存在 → `LubanError(RUN_NOT_FOUND)` → 404 |
| POST | `/v1/runs/{run_id}/cancel` | 取消 Run，返回 `{"cancelled": bool}` | Run 不存在 → 404 `RUN_NOT_FOUND`；存在但已终止/未注册取消句柄 → 正常返回 `cancelled=False`（`ctx.runtime.cancel()` 返回布尔而非抛错，来源：`runs.py:91-100`） |
| POST | `/v1/runs/{run_id}/resume` | **SSE 流式**从最近 checkpoint 续跑非终态 Run，帧格式同创建 Run；实现同样先 `await anext(stream)` 预热首事件，把校验失败在流真正开始前抛出走统一错误映射（来源：`runs.py:105-118`） | Run 或 checkpoint 不存在 → 404 `RUN_NOT_FOUND`（无 checkpoint 时 `details.reason="no_checkpoint"`）；Run 已终态/本进程仍在活跃执行/子 Run → 422 `VALIDATION_ERROR`；装配关闭 checkpoint（`create_app(checkpointer=None)`）→ 500 `CONFIG_ERROR`；校验全部在流开始前抛出，流中途失败以 `RUN_FAILED`/`RUN_CANCELLED` SSE 事件呈现 |

SSE 事件类型映射（供理解流式返回体裁，来源：`lubanagent/runtime/sse.py:10-27`）：仅 `SSE_EVENT_MAP` 覆盖的类型对外发送，共 15 条——`run.started`/`run.resumed`/`token.delta`/`tool.started`/`tool.finished`/`tool.failed`/`run.completed`/`run.failed`/`run.cancelled`/`tool.approval.requested`/`tool.approval.resolved`/`model.fallback`/`budget.warning`/`budget.exceeded`/`knowledge.no_hit`（知识全 miss 短路拒答；`run.resumed` 供 resume 续跑开始时发送，不重发 `run.started`）；未覆盖的内部 Trace 粒度事件（如 `STEP_*`/`MODEL_CALL_*`）默认不外发（`sse.py:34-41`）。事件格式：`event: {name}\ndata: {json}\n\n`，data 结构为 `{run_id, type, **payload}`（payload 经 `redact_value` 脱敏副本，`sse.py:40`）。

#### models.py（`lubanagent/api/routes/models.py`）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/models` | 列出模型注册（脱敏：不输出 `base_url` 等连接信息；`params` 经 `redact_registered_model_params` 脱敏——仅展示 openai_compat 白名单推理/计价字段，其余键值替换 `[REDACTED]`，`routes/models.py:14-25`） | 无 |
| PUT | `/v1/models` | Upsert 模型定义，请求体 `ModelDefinition`，返回脱敏后的定义；写入边界校验：请求体先经 `ModelDefinition` 校验器（`api_key_env`/`base_url`/`params` 连接边界与白名单，`lubanagent/core/model.py:148-170`）拦截为 422，仓储 `upsert_model` 写入前再校验一层；更新请求 `base_url` 为空表示沿用旧值（列表接口不返回旧连接地址） | `api_key_env`/`base_url`/`params` 触发连接边界规则 → 422 `VALIDATION_ERROR`（校验错误不回显输入值） |
| GET | `/v1/brain-mappings` | 查询四槽位（fast/smart/cheap/vision）当前映射；query `env`（缺省取 `ctx.settings.env`） | 无（未映射槽位跳过，不报错，见 `models.py:49-55`） |
| PUT | `/v1/brain-mappings` | 设置某槽位映射，请求体 `BrainMappingSetRequest{profile, model_key, env?}` | `model_key` 不存在 → `LubanError(MODEL_NOT_FOUND)` → 404 |

#### tools.py（`lubanagent/api/routes/tools.py`）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/tools` | 列出 Registry 已注册工具的 `ToolDescriptor` | 无 |
| POST | `/v1/tools/{key}/test` | 在线测试工具：`debug_tool` 直接执行，**不经 Runtime**（不产生 Run/Trace），请求体为任意 JSON（`dict[str, Any] | None`） | 工具未注册 → `ctx.registry.get_tool(key)` 触发 `LubanError(TOOL_NOT_FOUND)` → 404（来源：`tools.py:21-22` 注释） |

#### traces.py（`lubanagent/api/routes/traces.py`）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/traces/{run_id}` | 按 `run_id` 查询 Trace 头 + 全部事件，返回 `TraceOut{trace, events}` | Trace 不存在 → `LubanError(RUN_NOT_FOUND)` → 404（注意：此处复用 `RUN_NOT_FOUND` 而非独立 Trace 错误码，来源：`traces.py:18-23`） |

#### approvals.py（`lubanagent/api/routes/approvals.py`）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/approvals` | 列出待决审批（`ApprovalManager.pending()`），query `run_id` 可选过滤；持久化装配下（`create_app` 默认注入 `DbApprovalStore`）列表跨进程重启仍可见 | 无 |
| POST | `/v1/approvals/{approval_id}/decide` | 人工决定审批，请求体 `DecideRequest{approved, decided_by}`；`decided_by` 仅兼容旧客户端，实际决定人取 `get_principal()` 的 `Principal.subject`，响应返回终态、决定人及决定时间（`routes/approvals.py:39-56`）；decide 经 store 条件更新原子裁决，并发双 decide 恰一个成功，决定随审批记录落库 | 审批不存在或状态已非 `pending`（含已被并发决定）→ 路由手工返回 404 `ErrorOut(VALIDATION_ERROR)`（`approvals.py:46-50`） |

#### evals.py（`lubanagent/api/routes/evals.py`）—— 评测报告列表与详情（读真实 `eval_results`，R5 打通）

报告由 `luban eval run` 落库；API 只读展示，不承担评测执行（评测入口在 CLI，回归门禁语义见契约 D2-1 决策 4，来源：`evals.py:1-5` 模块 docstring）。响应模型直接复用 core 领域对象 `EvalReportSummary`/`EvalReportDetail`（`lubanagent/core/evaluation.py:147-165`），不做映射裁剪；旧 stub schema `EvalRunOut`/`EvalResultOut` 已随打通删除。

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/evals` | 报告摘要列表（`list[EvalReportSummary]`，按 `id` 倒序）；query：`dataset_key`、`agent_key`（均可选过滤）、`limit`（1-500，默认 50，`Query(ge=1, le=500)`）；读 `EvalRepository.list_summaries`（聚合列直出，不解析全量报告 JSON，来源：`evals.py:16-27`） | 无特殊错误（空库返回空列表） |
| GET | `/v1/evals/{result_id}` | 报告详情 `EvalReportDetail`：摘要字段 + 完整逐 case 结果（pass/fail、断言明细、judge 结论、run_id/output/error），读 `EvalRepository.get_detail`（来源：`evals.py:30-41`） | `result_id` 不存在 → `LubanError(EVAL_RESULT_NOT_FOUND)` → 404（新错误码，已入 `ERROR_HTTP_STATUS`，`api/errors.py:27`） |

旧 `GET /v1/evals/results` stub 路由已删除（2026-09 R5 打通时移除，`tests/lubanagent/api/test_stubs.py` 同步清理）。

#### graphs.py（`lubanagent/api/routes/graphs.py`）—— 图只读结构视图

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/graphs` | 列出 registry 已注册的确定性流程图，返回 `list[GraphSummaryOut{name, description, structure_available}>`；`structure_available` = `builder()` 返回 GraphBuilder（DSL 构建）才有结构视图 | 无（空列表正常返回） |
| GET | `/v1/graphs/{name}` | 图拓扑详情 `GraphDetailOut{name, description, entry, nodes, edges, cond_edges}`：请求时实时调 `descriptor.builder()` 读 DSL 结构（不触发 LangGraph 编译、不缓存不持久化）；node.kind ∈ `step/agent/human`，agent 节点带 `agent_key/input_from/output_to`、human 节点带 `prompt`、step 节点带 `summary`（fn docstring 首行），可执行体本身不序列化 | 未注册 → 404 `GRAPH_NOT_FOUND`（registry 内部抛 `VALIDATION_ERROR`，路由层转 404）；builder 返回非 GraphBuilder → 422 `GRAPH_STRUCTURE_UNAVAILABLE` |

数据来源是 `GraphBuilder.structure()` 只读访问器（`lubanagent/graph/builder.py`，不校验不编译）。只读定位：编辑走代码（`app/graphs/`），工作台「流程图」页消费这两端点渲染拓扑，不做图设计器。

#### skills.py（`lubanagent/api/routes/skills.py`）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/skills` | 列出已装载 Skill 包（读 `request.app.state.skill_registry` 内存视图，非 DB） | `skill_registry` 未装配（`app.state` 无此属性）→ 静默返回 `[]`，不报错（来源：`skills.py:37-40`） |
| POST | `/v1/skills` | 上传 Skill zip 安装（201），`multipart/form-data`，字段 `file`（`UploadFile`），上限 10MB | `skill_registry` 未装配 → `LubanError(CONFIG_ERROR)`；路由按 64 KiB 块读取，超过 `_MAX_SKILL_ZIP_BYTES`（10 MiB）立即拒绝且不调用 registry；ZIP 展开另受成员数/单成员/总量配额约束（`routes/skills.py:18-19、45-62`；`skill/registry.py:26-28、131-173`） |
| DELETE | `/v1/skills/{key}` | 删除已安装 Skill（204） | `skill_registry` 未装配 → `LubanError(CONFIG_ERROR)`；`key` 不存在 → `LubanError(VALIDATION_ERROR)` |

skills 路由的特殊架构约束：**不 import `lubanagent.skill` 模块**，只经 `request.app.state.skill_registry` 取用（用户领地 `app/main.py` 挂载），因为"api → skill 为分层禁忌"（来源：`skills.py:1-7` 模块 docstring）。source of truth 是文件系统（`skills_root/{name}/`），不是 DB。

#### knowledge.py（`lubanagent/api/routes/knowledge.py`）—— 工作台知识库管理

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/knowledge/sources` | 列出全部知识源：DB 托管行（managed=true）∪ registry 里不在 DB 的内置源（managed=false，如 project-docs/product-faq） | `knowledge_manager` 未装配 → `LubanError(CONFIG_ERROR)` |
| POST | `/v1/knowledge/sources` | multipart 上传多文件创建托管源（201）：`name`（Form pattern `^[a-z0-9][a-z0-9_-]{1,63}$`）/`description`/`chunk_size`(gt=0, 默认500)/`overlap`(ge=0, 默认50)/`files`（多文件，白名单 `.md/.txt/.pdf/.docx/.csv/.json/.html/.xml`）；文件落盘 `knowledge_files_root/<name>/`、切块入库、注册 connector | 重名 → 409 `conflict_response`（`knowledge.py:68-69`）；超过 50 文件在读取前拒绝；路由按 64 KiB 块检查单文件 10 MiB、请求累计 50 MiB，上限外不调用 manager；坏后缀/坏参数由 manager 校验 → 422（`routes/knowledge.py:25-29、72-110`） |
| DELETE | `/v1/knowledge/sources/{name}` | 删除托管源（204）：块 + 落盘目录 + DB 行 + registry 注册全清 | 不存在 → 404 `KNOWLEDGE_SOURCE_NOT_FOUND`；非托管源（代码种子源）→ 422 `VALIDATION_ERROR` |
| POST | `/v1/knowledge/sources/{name}/rechunk` | 按新参数从落盘文件重新切块灌库（200），body `KnowledgeRechunkRequest{chunk_size, overlap}` | 不存在 → 404；参数非法/落盘文件缺失 → 422 |
| GET | `/v1/knowledge/sources/{name}/chunks` | 查看切块明细，query `limit`（1-1000，默认 200），返回无 embedding 字段的 `KnowledgeChunkOut` | 不存在 → 404 `KNOWLEDGE_SOURCE_NOT_FOUND` |
| POST | `/v1/knowledge/query` | 检索测试（不经 Run/守门），body `KnowledgeQueryRequest{query, source?, top_k(1-50, 默认5)}`；`source=None` 检索全部源；返回 score 降序的 `list[KnowledgeHitOut]` | 未知源 → 422 `VALIDATION_ERROR` |

knowledge 路由与 skills 同款架构约束：**不 import `lubanagent.knowledge` 模块**（"api → knowledge 为分层禁忌"，import-linter 守卫），只经 `request.app.state.knowledge_manager` 鸭子类型取用；但与 skills 不同，manager 由**框架工厂**（`lubanagent/app/factory.py:92-98`）而非用户领地装配挂载（来源：`knowledge.py:1-6` 模块 docstring）。

#### 其他（不在 `/v1` 前缀下、不经鉴权）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/health` | 健康检查，返回 `{"status": "ok"}` | 无（`lubanagent/app/factory.py:150-152`） |
| GET/HEAD | `/*`（`ui/dist` 存在时） | 静态资源托管；仅页面导航 404（非 `/v1`、非 `/assets/`、`/favicon*`、`/.well-known/`，且无文件后缀）回退 `index.html` | 静态文件缺失或其他非导航请求的 404 不回退；非 404 原状态保留（`factory.py:170-185`） |

### RPC / MQ / Cron

无。

### CLI（`lubanagent/cli/main.py`，Typer 应用，命令名 `luban`）

| 命令 | 参数 | 说明 | 错误情况 |
|---|---|---|---|
| `luban init` | `--agents-dir`（默认 `app/agents`）、`--database-url`（envvar `DATABASE_URL`）、`--fill-missing`（缺省 False，H08） | 先 `seed_defaults()` 幂等落种子（**模型定义与槽位映射都只填空位**，用户已配的真实模型映射/改过的同 key 定义不覆盖），再扫描 `agents_dir/*.yaml`（按文件名排序）逐个导入，四分支幂等（风险 R7 + H08，`main.py:121-150` `_init_async(definitions, url, *, fill_missing=False)`）：key 不存在 → `create_agent`（版本置 1）；`--fill-missing` 且 key 已存在 → **一律跳过不追加版本**（不比对内容，自动启动专用）；key 已存在且剔除 `version`/`status` 运行期字段后 `model_dump` 深度相等 → **跳过导入**（不追加版本）；有变化 → `create_version`（版本号 = 现有版本+1）。打印每个 `key@version`，未变化的追加 ` (unchanged)` 标记。YAML 的 `knowledge` 段支持两种形态：list shorthand（只设 sources）与 dict 全字段（`sources`/`top_k`/`cite`/守门三字段 `score_threshold`/`no_hit_fallback`/`max_age_days`，未知键 → `VALIDATION_ERROR`，`yaml_io.py:104-133`） | 任意异常经 `_fail()` 打印 `error: {code}: {message}` 到 stderr 并 `exit(1)` |
| `luban export <key>` | `--output`（缺省打印 stdout）、`--version`（缺省当前版）、`--database-url` | 导出指定 Agent（或指定版本快照）定义为 YAML shorthand，往返无损；knowledge 段导出规则：仅 sources 非空且其余字段全默认 → list shorthand，任一守门字段非默认 → dict（`yaml_io.py:234-258`） | key 不存在 → `AGENT_NOT_FOUND`；version 不存在 → `AGENT_VERSION_NOT_FOUND`；均经 `_fail()` → `exit(1)` |
| `luban migrate` | `--database-url` | 包 Alembic `upgrade head`（读仓库根 `alembic.ini` + `migrations/`），临时注入 `DATABASE_URL` 环境变量并在结束后还原，同时快照/还原 logging 配置（因 `migrations/env.py` 的 `fileConfig` 会重置进程日志） | Alembic 执行异常 → `_fail()` → `exit(1)` |
| `luban eval run <agent>` | `--dataset`（必填 YAML 路径）、`--compare`（版本号或基线报告 JSON 路径）、`--save`（报告输出路径）、`--auto-approve`（审批型工具自动放行真实执行，缺省仅断言调用意图）、`--database-url`；`agent` 支持 `key` 或 `key@version` | 跑评测数据集，逐 case 输出 PASS/FAIL，汇总 `pass_rate`；启动时独立进程装配三件套（扩展扫描 `discover_packages` + 知识源自举 `restore_knowledge_sources` + 审批 manager 按需注入，见调用树）；跑完**完整报告落库**（`EvalRepository.save_report`：数据集首次运行时登记 + `EvalReport` 全量序列化落 `report` 列 + 聚合列 `pass_rate`/`passed_cases`/`total_cases`/`wrong_answer_rate` + 回填实际命中的 `model_key`，供 `/v1/evals` 列表/详情读真实数据，`main.py:393-407`）；`--save` JSON 输出保留（可移植基线载体）；`--compare` 命中回归（`comparison.regressions` 非空）→ `exit(1)`（compare 走内存/JSON 文件基线，DB 不接入对比） | Agent/版本不存在等运行期错误 → `_fail_evaluation()` → `exit(2)`（与 `init`/`export`/`migrate` 的 `exit(1)` 不同，专用退出码区分"评测发现问题"与"命令执行失败"） |

## 数据模型

本模块无自己的 ORM 表——`lubanagent/api/` 只读写 `lubanagent.persistence` 的仓储（`AgentRepository`/`PromptRepository`/`ModelRepository`/`RunRepository`/`TraceRepository`），`lubanagent/cli/` 额外读写 `EvalRepository`；表结构定义见 persistence 模块资产文档。本节列出 `lubanagent/api/schemas.py` 中的请求/响应 Pydantic 模型字段清单（来源：`lubanagent/api/schemas.py`）：

| Schema | 字段 | 说明 |
|---|---|---|
| `RunCreateRequest` | `input: str`、`session_id: str\|None`、`user_id: str\|None`（`Field(default=None, max_length=128)`）、`version: int\|None`、`variables: dict[str,str]\|None`、`history: list[dict[str,str]]\|None`、`instructions_override: str\|None` | POST `/v1/agents/{key}/runs` 请求体；`user_id` 为透传观测标签（业务侧用户 id，引擎不做用户管理/鉴权，仅落库供 `GET /v1/runs?user_id=` 排查过滤）；`history` 为 OpenAI 风格 `[{role, content}]`，v1 不查库（注释标注 D3-2）；`instructions_override` 仅用于试跑临时替换 instructions，不入库 |
| `ModelDefinitionOut` | `key`、`provider`、`model_name`、`factory`、`capability: ModelCapability`、`params: dict`、`api_key_env: str\|None`、`enabled: bool` | 模型定义对外脱敏视图：只输出 `api_key_env`（变量名），不输出 `base_url` 等连接信息；`params` 值经 `redact_registered_model_params` 脱敏，非 openai_compat 白名单字段只显示 `[REDACTED]`（`routes/models.py:14-25`） |
| `BrainMappingSetRequest` | `profile: BrainProfile`、`model_key: str`、`env: str\|None` | PUT `/v1/brain-mappings` 请求体；`env` 缺省取 `settings.env` |
| `BrainMappingOut` | `env`、`profile: BrainProfile`、`model_key` | — |
| `TraceOut` | `trace: Trace`、`events: list[TraceEvent]` | 直接复用 core 领域模型，不做映射裁剪 |
| `ApprovalOut` | `id`、`run_id`、`tool_key`、`args: dict`、`created_at: datetime`、`status: str`、`decided_by: str\|None = None`、`decided_at: datetime\|None = None` | `core.ApprovalRequest` 领域对象的镜像（可持久化，落 `approvals` 表）；列表接口只返回 pending（`schemas.py:67-77`、`routes/approvals.py:31-36`） |
| `DecideRequest` | `approved: bool`、`decided_by: str = ""` | `decided_by` 仅请求兼容字段，不参与归因（`schemas.py:80-82`、`routes/approvals.py:51-52`） |
| `DecideOut` | `status: str`（`"approved"` \| `"rejected"`）、`decided_by: str\|None = None`、`decided_at: datetime\|None = None` | 来源于终态审批记录（`schemas.py:85-88`、`routes/approvals.py:53-56`） |
| `SkillOut` | `key`、`name`、`version`、`manifest: dict`、`enabled: bool = True` | — |
| `KnowledgeFileOut` | `filename: str`、`size: int` | 托管知识源落盘文件清单元素 |
| `KnowledgeSourceOut` | `name`、`description = ""`、`chunk_size: int\|None`、`overlap: int\|None`、`files: list[KnowledgeFileOut]`、`chunk_count = 0`、`managed = True`、`created_at/updated_at: datetime\|None` | `managed=False` 为代码种子源（参数不可改不可删）；非托管源 `chunk_size/overlap` 为 `None`（来源：`schemas.py:124-136`） |
| `KnowledgeRechunkRequest` | `chunk_size: int`（gt=0）、`overlap: int`（ge=0） | POST `/v1/knowledge/sources/{name}/rechunk` 请求体；`overlap < chunk_size` 由知识层校验 |
| `KnowledgeChunkOut` | `chunk_index: int`、`content: str`、`metadata: dict`、`created_at: datetime` | 切块明细视图，**无 embedding 字段** |
| `KnowledgeQueryRequest` | `query: str`（min_length=1）、`source: str\|None = None`、`top_k: int = 5`（1-50） | POST `/v1/knowledge/query` 请求体；`source=None` 检索全部知识源 |
| `KnowledgeHitOut` | `source: str`、`chunk_index: int`、`content: str`、`score: float`、`metadata: dict` | 检索测试命中项，score 降序返回 |
| `ErrorBody` | `code: str`、`message: str`、`details: dict = {}`、`retryable: bool = False` | — |
| `ErrorOut` | `error: ErrorBody`；工厂方法 `ErrorOut.of(code, message, details=, retryable=)` | 统一错误响应包络 `{"error": {...}}` |

镜像别名（不重新定义，直接复用 core 领域模型作为 `response_model`）：`AgentDefinitionOut = AgentDefinition`、`RunOut = Run`（来源：`schemas.py:17-19`）；prompts 路由同样直接以 `PromptDefinition`/`PromptVersion` 为请求/响应模型，不做映射裁剪（来源：`routes/prompts.py:7`）；evals 路由以 `EvalReportSummary`/`EvalReportDetail`（`lubanagent/core/evaluation.py`）为响应模型（来源：`routes/evals.py:10`）。

## 行为

### 状态机

无（本模块不定义业务状态机；Run/审批状态机归属各自领域模块）。

### 调用树

#### `create_app()`（`lubanagent/app/factory.py:48-227`）

用户装配根（`app/main.py::create_app`）或直接调用框架 `create_app()` 时触发，一次性完成"配置加载 → DB 建立 → 扩展发现 → 观测装配 → 运行时构建 → FastAPI 组装 → 静态资源挂载"全过程，返回可直接 `uvicorn` 启动的 `FastAPI` 实例。

```
create_app(settings?, registry?, db?, on_startup?, extra_routers=(), ui_dist=None, skill_registry?, summarizer?, mcp_registry?, user_key_fn?, checkpointer=_CHECKPOINTER_UNSET)
├── 1. settings = settings or get_settings()  # 配置与日志
│   └── configure_logging(log_level, log_format)
├── 2. db = db or create_database(settings)  # 迁移不随启动自动跑，走 `luban migrate`
├── 3. registry = registry or default_registry
│   ├── for package in settings.extension_packages: discover([package], registry)
│   │   └── ModuleNotFoundError 且 exc.name == package → warning 跳过（空脚手架也能起）
│   └── auth_enabled 且无 api_key 且 registry.get_auth() is None → raise LubanError(CONFIG_ERROR)
│       # 装配期 fail fast（风险 R6，factory.py:60-77）：该组合下默认 AuthProvider 会拒绝所有
│       # /v1 请求（起服务只会"表面起来了但接口全挂"），直接拒绝启动并给三条出路：
│       # (1) 设 API_KEY（配合 AUTH_ENABLED=true）；(2) Registry 注册自定义 AuthProvider；
│       # (3) 关闭鉴权（AUTH_ENABLED=false，仅限开发期零配置）。放在 discover 之后：用户扩展可注册 AuthProvider。
├── 4. observability = resolve_backend(settings, registry)
│   └── isinstance(SafeBackend) → observability.set_sink(db_failure_sink(db))  # 外发失败落 export_failures
│       + observability.set_retry_db(db)  # drain 时回放待重试留痕（成功删/失败计次/达上限放弃，factory.py:84）
├── 5. checkpointer：哨兵语义（`factory.py:43-45、109-110`）——未传/缺省 = LubanCheckpointer(db)
│   （普通 Run 逐步 checkpoint + resume 默认启用）；显式传 None = 关闭（零 checkpoint 写入，
│   resume 显式 CONFIG_ERROR）；approval_manager = ApprovalManager(store=DbApprovalStore(db))
│   # 审批记录落库（R11：决定不随进程重启丢失）；runtime = AgentRuntime(db, settings, registry,
│   observability, approval_manager, checkpointer=checkpointer)
├── 5.5 知识库装配（factory.py:80-98）：embedder 选型（settings.embedding_model 非空 →
│   OpenAICompatibleEmbedding，空 → FakeEmbedding）+ KnowledgeStore(db) +
│   KnowledgeSourceManager(db, store, embedder, registry, files_root=settings.knowledge_files_root)
├── 6. app = FastAPI(lifespan=...)
│   ├── app.state.ctx = AppContext(settings, db, registry, runtime, approval_manager, rate_limiter)
│   ├── app.state.knowledge_store / knowledge_embedder / knowledge_manager  # 种子源与 knowledge 路由共用同一实例（factory.py:126-129）
│   ├── register_error_handlers(app)  # RequestValidationError → LubanError → Exception 顺序注册
│   ├── GET /health（无鉴权）
│   ├── app.include_router(api_router)  # 框架 /v1/* 路由
│   └── for router in extra_routers: app.include_router(router, dependencies=[Depends(get_principal)])
└── 7. [条件] ui_dist（缺省 lubanagent/ui/dist）存在
    ├── 是 → 路由之后挂 StaticFiles("/")；仅 GET/HEAD 页面导航 404 回退 index.html
    │         /v1、静态目录和有文件后缀的请求保持 404
    └── 否 → 跳过（开发期前端独立 `npm run dev`）
```

lifespan 钩子（`factory.py:150-172`）：启动时若装配了 `mcp_registry` 先登记 `mcp_servers` 表 enabled 记录（失败不阻断启动），再 `await knowledge_manager.ensure_restored()`（读 `knowledge_sources` 全表恢复托管知识源的 registry 注册，失败仅告警不阻断启动），再执行传入的 `on_startup` 回调（用户领地挂扩展/灌知识源，因 FastAPI `on_event("startup")` 已被 `lifespan` 取代）；关闭时若 `observability` 是 `SafeBackend` 则 `await observability.drain()`（外发收尾）、`mcp_registry.aclose()`（MCP 长连释放），再 `await db.close()`（释放连接池）。

#### `seed_defaults()`（`lubanagent/app/lifespan.py:11-29`）

```
seed_defaults(db)
├── env = get_settings().env
└── async with db.session()
    ├── for payload in defaults.SEED_MODEL_DEFINITIONS:
    │   └── if await repo.get_model(payload["key"]) is None: repo.upsert_model(...)  # 只填不存在（H08）
    └── for profile, model_key in defaults.SEED_BRAIN_MAPPINGS.items():
        └── if not repo.has_brain_mapping(env, profile): repo.set_brain_mapping(env, profile, model_key)  # 只填空位
```

供 `luban init` 与装配期测试共用；**模型定义与槽位映射都只填空位（H08）**——种子模型仅当 `get_model(key) is None` 时才 `upsert_model`（此前每次 init 无条件 upsert，会把工作台改过的同 key 定义覆盖回种子值），已存在的映射（用户经工作台/API 配置过）不覆盖，因此重复 `luban init`（如 `dev.sh` 每次重启都会跑）既不会把真实模型打回 `fake-chat`，也不会把工作台的模型修改打回。

#### `luban init`（`lubanagent/cli/main.py:85-110`）

```
init(agents_dir="app/agents", database_url?, fill_missing=False)
├── definitions = _load_definitions(agents_dir)  # 同步侧：扫描 *.yaml（按文件名排序），yaml_io.definition_from_yaml 解析
└── asyncio.run(_init_async(definitions, url, fill_missing=fill_missing))
    ├── seed_defaults(db)  # 种子只填不存在（见上节）
    └── for definition in definitions:
        ├── [key 不存在] definition.version = 1; repo.create_agent(definition)
        ├── [key 已存在 且 fill_missing] 跳过，输出 key@existing.version (unchanged)
        │   └── 不比对内容，磁盘旧 YAML 不打回工作台修改（H08，main.py:136-138）
        ├── [key 已存在 且 _strip_runtime_fields(definition) == _strip_runtime_fields(existing)]
        │   └── 跳过导入，输出 key@existing.version (unchanged)  # 防重复执行造成版本膨胀（风险 R7）
        └── [key 已存在 且内容有变化] definition.version = existing.version + 1; repo.create_version(key, definition)
```

`_strip_runtime_fields`（`main.py:113-118`）剔除 `version`/`status` 后按 `model_dump(mode="json")` 深度比较，只比对定义内容。`scripts/dev.sh` 自动启动改用 `luban init --fill-missing`（H08）：只补缺失 Agent，已存在的一律不追加版本；用户显式导入（不带该选项）保持原语义，内容变化仍追加版本。

#### `luban migrate`（`lubanagent/cli/main.py:221-246`）

```
migrate(database_url?)
├── AlembicConfig(仓库根 alembic.ini) + script_location=migrations/ + sqlalchemy.url=sync_database_url
├── 临时置 os.environ["DATABASE_URL"] = resolved（migrations/env.py 会重取 Settings() 的 URL）
├── 快照进程 logging（fileConfig 会重置宿主进程日志）
├── alembic_command.upgrade(config, "head")
└── finally: 还原 logging 快照 + 还原/清除 DATABASE_URL 环境变量
```

#### `luban eval run`（`lubanagent/cli/main.py:278-417`）

```
eval_run(agent, dataset, compare?, save?, auto_approve?, database_url?)
├── key, version = _parse_agent_spec(agent)  # "key@3" → (key, 3)；@ 后非数字 → VALIDATION_ERROR
├── dataset_obj = load_dataset(dataset)
├── baseline_json = compare 非纯数字时读取该路径文件内容（同步侧 IO）
└── asyncio.run(_eval_run_async(...))
    ├── Settings.load() 后独立进程装配三件套（风险 R12 修复，eval 不经 create_app）：
    │   ├── discover_packages(settings.extension_packages)  # 扩展扫描（与 factory 共用容错助手：顶层包不存在记 warning 跳过）
    │   ├── restore_knowledge_sources(db, settings)  # 知识源自举：按库中已有 chunk 重建通用 SqlKnowledgeConnector
    │   │   └── 仅覆盖「有 chunk 且注册表无连接器」的 source；自定义 connector 不覆盖；无 chunk 的引用仍运行期报错
    │   └── [auto_approve] approval_manager = AutoApproveManager()  # 请求即批；缺省 None——审批型工具执行被闸门拦截
    └── runtime 构造（AgentRuntime(checkpointer=LubanCheckpointer(db))，`main.py:399-405`）——
        CLI eval 独立进程与 API 服务同语义装配（缺省启用 checkpoint，中断的 eval Run 可 resume；
        仅本次进程，不跨进程续跑）
    ├── 入口校验：agent/version 不存在 → LubanError(AGENT_NOT_FOUND/AGENT_VERSION_NOT_FOUND)（运行错误，非逐 case fail）
    ├── [数据集含 judge case] 用 BrainResolver 解析 SMART 槽位模型 → LlmJudge
    ├── runner.run_dataset(key, dataset_obj, version)  # EvalRunner 实际执行
    ├── 完整报告落库（main.py:393-407）：EvalRepository.create_dataset（数据集首次登记）
    │   ├── model_key 回填：取首个有 model_key 的子 Run 实际命中模型（runner 本身不知道模型解析结果）
    │   └── save_report(report)  # 完整 EvalReport 序列化落 report 列 + 聚合列（pass_rate/passed/total/错答率）
    └── [compare 不为 None]
        ├── [baseline_json 存在] EvalReport.from_json(baseline_json)
        └── [否则] runner.run_dataset(key, dataset_obj, version=int(compare))  # 重跑基线版本
        └── comparison = compare_reports(baseline, report)
```
CLI 侧输出：逐 case `[PASS]`/`[FAIL]` + 失败原因；`pass_rate` 汇总；`--save` 落 JSON；有 `compare` 则打印 `regressions`/`improvements`/`pass_rate_delta`，`regressions` 非空触发 `exit(1)`。

**`--auto-approve` 语义**（`AutoApproveManager`，`tool/approval.py:198-224`）：审批型（高风险）工具在评测进程里没有人工可等——缺省不配 manager 时工具执行被闸门拦截（`ToolApprovalRequiredError` 进 tool result，模型可见并可向用户解释），而 `expect_tools` 断言按 **TOOL_CALL_STARTED（调用意图）** 判定，不要求执行成功；`--auto-approve` 让请求即批、工具真实执行（Trace 事件流 requested→resolved 不变，决定人标记 `auto-approve`，不是人工主体），适用于断言依赖执行结果（如回复须含退款单号）的场景。**高风险工具会被真实执行**——仅用于数据集指向 Mock/受控环境的评测。评测进程缺省不注入 DB store（`AutoApproveManager()` 内存 store，`--database-url` 用途不含审批落库）。

## 边界规则

- **鉴权强制点**：框架 `api_router` 聚合依赖覆盖框架 `/v1/*`；用户 router 必须显式保护，工厂为 `extra_routers` 再注入 `get_principal` 并在静态挂载前注册（`factory.py:154-185`），示例 `teams.router` 显式依赖（`app/api/teams.py:12-16`）。`/health` 不鉴权；`/v1` 未知路由不会被 SPA 页面回退接管。
- **鉴权缺省行为**：`ctx.settings.auth_enabled=False` 时直接使用 `Principal(subject="anonymous")`，不执行身份校验但仍限流（`api/auth.py:76-94`）；此时接口对外无认证保护，需运维层自行加防护。
- **鉴权优先级与配置缺失 fail fast**：`auth_enabled=True` 时优先用 `registry.get_auth()`（用户自定义 `AuthProvider`），未注册则退化到框架内置 `ApiKeyAuthProvider(settings.api_key)`。若开启鉴权但既未配置 `api_key` 又未注册自定义 provider，`create_app()` **装配期（扩展扫描后）直接 raise `LubanError(CONFIG_ERROR)` 拒绝启动**，错误信息给出三条出路（设 API_KEY / 注册自定义 AuthProvider / 关闭鉴权）（风险 R6，来源：`lubanagent/app/factory.py:60-77`）。`ApiKeyAuthProvider.verify()` 运行时的同款 `CONFIG_ERROR` 分支保留为纵深防御——仅供绕过工厂直接构造 `ApiKeyAuthProvider(None)` 的场景兜底（来源：`auth.py:22-26` 注释）。
- **SPA 回退边界**：有 UI dist 时仅 GET/HEAD 且无文件后缀、非 `/v1`/`/v1/*`、非 `/assets/`/`/favicon*`/`/.well-known/` 的 404 返回 `index.html`；其他错误沿用 HTTP 异常响应，未知 API 保持 JSON 404（`factory.py:170-185`）。
- **限流规则**：`auth_enabled=True` 时先由 `AuthProvider.verify()` 验证，再按返回的 `Principal.subject` 分桶；关闭鉴权时所有请求统一使用 `anonymous` 桶，不采信任意 Bearer 或 `X-Forwarded-For` 拆桶（`api/auth.py:76-94`）。每桶每 60 秒最多 `settings.rate_limit_per_minute` 次（滑动窗口）；`InMemoryRateLimiter` 默认最多 4096 桶，每次调用清理 60 秒窗口内已过期的命中，空桶回收；容量已满时拒绝新主体而不驱逐仍有命中的桶。被限流请求返回 `RATE_LIMITED` 429（`api/auth.py:46-73、91-94`、`api/errors.py:29`）。计数只在单进程内有效，多进程/多实例不共享；当前不使用转发 IP 作为身份。内置共享 API Key 验证后统一返回 `api-key-holder`，只能证明持有该 Key，不能识别独立人员（`api/auth.py:21-30`）。
- **错误码 → HTTP 状态映射钉死在 `ERROR_HTTP_STATUS` 字典**（`errors.py:16-32`），未覆盖的错误码默认映射到 500；异常处理器注册顺序固定为 `RequestValidationError → LubanError → Exception`（`errors.py:42`），未捕获异常只记录堆栈到日志，响应体不泄露堆栈信息（`errors.py:61-65`）。
- **409 冲突走路由级手工构造而非 `LubanError`**：Agent key 重复、版本已存在（`agents.py`）与 Prompt key 重复、版本已存在（`prompts.py`）均用 `conflict_response()` 直接返回 `JSONResponse(409, ...)`，未定义独立 `ErrorCode`，复用 `VALIDATION_ERROR` 语义（来源：`errors.py:35-38`）。审批"不存在或已终结"同样手工构造 404（`approvals.py:46-50`），不经统一异常处理器。
- **Agent 版本不可覆盖**：`POST /v1/agents/{key}/versions` 若目标版本号已存在直接 409 拒绝创建，版本快照一旦写入不可变更（来源：`agents.py:51-55`）。
- **`luban migrate` / `luban init` 幂等性**：`migrate` 底层是 Alembic `upgrade head`，重复执行在已是最新版本时为空操作，安全；`init` 的 `seed_defaults()` 种子模型与映射**只填不存在**（H08，`lifespan.py:21-29`），agents 目录导入默认三分支幂等（风险 R7 修复）——已存在 key 且剔除 `version`/`status` 后内容深度相等时**跳过导入不追加版本**（`_strip_runtime_fields` 比较，`main.py:113-118、139-143`），只有内容真正变化才 `create_version`；`--fill-missing` 下已存在一律跳过不追加版本（`main.py:136-138`），供自动启动使用（`scripts/dev.sh:36-37`）。Docker 启动脚本注释明确写"migrate/init 幂等，重起安全（契约 D1-9 决策）"（来源：`docker/Dockerfile:39`），该表述现在对 `init` 全程成立（如 `dev.sh` 每次重启跑 `init` 不会造成版本号膨胀）。
- **`luban eval run` 独立退出码**：评测运行期错误（Agent/版本不存在等）用 `exit(2)`，区别于 `init`/`export`/`migrate` 统一用的 `exit(1)`；`--compare` 命中回归（`regressions` 非空）用 `exit(1)`（来源：`main.py:269-275、338-339`）——CI 集成时需按这两种退出码区分"评测流程本身出错"与"发现质量回归"。
- **演示部署边界（H08）**：仓库根 `docker-compose.yml` 是全栈演示形态，不是生产拓扑——app（`docker-compose.yml:10-12`）与 Langfuse 工作台（`docker-compose.yml:58-60`）端口都只绑 `127.0.0.1` loopback，`AUTH_ENABLED=false` 仅出现在演示 app 服务且注释标注生产须 `true` + `API_KEY`（`docker-compose.yml:19-20`）；生产部署用反向代理 + 鉴权开启。mock-biz 不映射宿主端口，app 经容器网络 `http://mock-biz:9100` 访问（`docker-compose.yml:18、91`）。工作台知识库/头像文件经 `KNOWLEDGE_FILES_ROOT=/data/knowledge`、`AVATAR_FILES_ROOT=/data/avatars` 挂 `knowledge-data`/`avatars-data` 具名卷持久化，容器重建不丢（`docker-compose.yml:21-27、96-97`）。本地开发 `scripts/dev.sh` 同口径：API uvicorn 绑 `127.0.0.1:8000`（`dev.sh:38-39`）、Vite 显式 `--host 127.0.0.1 --port 5173 --strictPort`（`dev.sh:49`）、自动启动用 `luban init --fill-missing`（`dev.sh:36-37`）。Docker 构建上下文裁剪（仓库根 `.dockerignore`，构建上下文为仓库根）：`**/.env`/`**/.env.*` 递归排除且仅放行样本 `app/config/.env.example`、私钥形态 `**/*.pem`/`**/*.key`/`**/*.p12`/`**/*.pfx`/`**/id_rsa*`/`**/id_ed25519*`、运行数据 `/data/`/`demos/customer_service/data/`、调试产物 `/.study/`（`.dockerignore:37-53`）；canary 实测镜像构建上下文归 H14。以上部署契约由 `tests/test_deployment_contracts.py` 固化（dockerignore canary 匹配、compose loopback/卷/鉴权边界、dev.sh loopback、种子只填不存在、`--fill-missing` 幂等与显式导入追加版本）。
- **skills 路由分层禁忌**：`api/routes/skills.py` 不得 `import lubanagent.skill`，只能经 `request.app.state.skill_registry` 取用；`skill_registry` 未挂载时 `list_skills` 静默返回空列表，但 `upload_skill`/`delete_skill` 会显式抛 `CONFIG_ERROR`（来源：`skills.py:1-7,37-48`）——即"读"与"写"在未装配场景下的行为不对称。**knowledge 路由同款禁忌**：`api/routes/knowledge.py` 不得 `import lubanagent.knowledge`，经 `app.state.knowledge_manager` 取用（框架工厂装配，未装配抛 `CONFIG_ERROR`）；新错误码 `KNOWLEDGE_SOURCE_NOT_FOUND` → 404 已入 `ERROR_HTTP_STATUS`（来源：`knowledge.py:1-6,31-36`、`api/errors.py:24`）。**graphs 路由无分层禁忌**：直接 `import lubanagent.graph.GraphBuilder`（结构视图消费 DSL 层，非执行层）；新错误码 `GRAPH_NOT_FOUND` → 404、`GRAPH_STRUCTURE_UNAVAILABLE` → 422 已入 `ERROR_HTTP_STATUS`（来源：`graphs.py:1-8`、`api/errors.py:24-25,33`）。
- **multipart 上传接收边界**：`skills`、`avatars`、`knowledge` 路由在 `UploadFile` 已由 ASGI multipart 解析后，按 64 KiB 块累计读取；分别在 ZIP 10 MiB、形象 20 MiB、知识单文件 10 MiB/请求累计 50 MiB/最多 50 文件处拒绝，超额时不进入下游安装/保存/创建（`routes/skills.py:18-19、45-62`；`routes/avatars.py:23-24、64-85`；`routes/knowledge.py:25-29、72-110`）。合格文件会合并到内存；这些检查不约束 multipart 解析前的请求体/并发，部署网关仍须限制请求体大小及并发，不能据此宣称全过程内存有界。
- **knowledge 上传的路由/管理层分工**：路由先检查文件数，逐块检查单文件/累计字节，再做重名预检（409）；manager 再验证后缀、大小、切块参数、净化文件名，失败为 `VALIDATION_ERROR`；FastAPI Form 校验也可能返回 422（`routes/knowledge.py:63-110`；`knowledge/manager.py:189-205、365-392`）。
- **`debug_tool` 不产生 Run/Trace**：`/v1/tools/{key}/test` 直接执行工具，绕过 `AgentRuntime`，因此不会写入 Run/Trace 记录，也不受预算/步数等治理约束（来源：`tools.py:1,19-23` 注释）。
- **SSE 流式响应的错误语义分层**：Run 启动前的失败（Agent/版本不存在、不可运行等）走 HTTP 层错误映射（404/422）；Run 已开始后的失败走 SSE 事件（`run.failed`/`run.cancelled`），HTTP 状态码本身仍是 200（来源：`runs.py:48-54`，resume 端点同模式 `runs.py:105-118`）——**客户端必须同时处理 HTTP 错误响应和 SSE 内的终态事件两种失败信道**。
- **resume 仅限本进程非终态 Run**：`POST /v1/runs/{run_id}/resume` 五项前置校验（Run 存在、非终态、非本进程活跃执行、非子 Run、存在 checkpoint）任一失败在流开始前抛错；子 Run 不可独立 resume（归属父编排 Run）；进程内并发 resume 由 per-run `asyncio.Lock` 串行（锁在全部校验之前获取）——第二个请求等锁到第一个推进终态后，按**终态拒绝**（`VALIDATION_ERROR`）而非活跃拒绝（测试 `test_resume.py::test_resume_concurrent_serialized` 明确断言该语义）；跨进程恢复（如 eval 独立进程）不在契约内（来源：`runs.py:105-118`、`runtime/agent_loop.py` `stream_resume`）。
- **`create_app(checkpointer=...)` 哨兵语义**：缺省自动装配 `LubanCheckpointer(db)`（resume 默认可用）；显式传 `None` 才关闭 checkpoint 写入，此时 resume 返回 500 `CONFIG_ERROR` 而非静默看似可用（来源：`factory.py:43-45、109-110`）。
- **模型注册输出/写入的防密钥边界**：模型列表与详情（`GET /v1/models`、`PUT /v1/models` 响应）的 `params` 经 `redact_registered_model_params` 脱敏——管理界面仅展示 openai_compat 已知推理/计价字段，其余键（含非 openai_compat 工厂全部字段）值替换 `[REDACTED]`，历史脏数据可展示但秘密不回显（来源：`routes/models.py:14-25`、`lubanagent/core/model.py:55-64`）。写入侧连接边界校验见 persistence 模块（`ModelRepository.upsert_model` → `validate_model_connection`）；`base_url` 不回显，更新时传空表示沿用旧值。

## 名词

| 术语 | 语义 |
|---|---|
| `AuthProvider` | `lubanagent.extension` 定义的鉴权扩展点 Protocol（`async def verify(request: AuthRequest) -> Principal`），用户可经 `Registry.register_auth()` 注册自定义实现替换框架内置 `ApiKeyAuthProvider`；验证失败须抛 `LubanError(AUTH_FAILED)`（来源：`lubanagent/extension/protocols.py:46-48`） |
| `Principal` | 框架中立的鉴权主体结果，字段 `subject: str`、`scopes: list[str]`、`claims: dict`；`auth_enabled=False` 时固定为 `Principal(subject="anonymous")`（来源：`protocols.py:20-25`、`api/auth.py:86-91`） |
| `AuthRequest` | 框架中立的鉴权请求载体，`headers`/`query_params` 均已由 `api/auth.py` 从 FastAPI `Request` 小写化适配后传入，与具体 Web 框架解耦（来源：`protocols.py:28-32`） |
| `AppContext` | 挂在 `app.state.ctx` 上的应用装配产物 dataclass，聚合 `settings/db/registry/runtime/approval_manager/rate_limiter`；测试可整体替换或经 FastAPI `dependency_overrides` 注入替身（来源：`deps.py:18-27`） |
| 装配根 | `create_app()` 的角色定位——框架侧 `lubanagent/app/factory.py::create_app` 是"框架装配根"，用户侧 `app/main.py::create_app` 是"用户装配根"（调用框架版本 + 挂业务扩展），二者同名但分属不同领地 |
| `RateLimiter` | `api/auth.py:42-73` 定义的限流 Protocol（`async def allow(key) -> bool`）及内置 `InMemoryRateLimiter`；单进程滑动窗口、默认最多 4096 个主体桶，非分布式（多进程/多实例部署下各自独立计数） |
| `debug_tool` | `lubanagent.tool` 提供的工具直执行函数，供 `/v1/tools/{key}/test` 调用，绕过 `AgentRuntime` 治理链路（步数/预算/超时均不生效） |
| 评测领域模型 | `lubanagent/core/evaluation.py` 的 `EvalCase`/`EvalDataset`/`EvalReport`/`EvalReportSummary`/`EvalReportDetail` 等；自 observability/evals 下沉 core（persistence 仓储需要领域对象承载，分层契约禁止 persistence → observability），旧路径 `lubanagent.observability.evals` 保留 re-export 垫片，既有导入零改动（来源：`core/evaluation.py:1-7` 模块 docstring） |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.persistence` | 所有 `db.session()` 调用点 | `AgentRepository`/`PromptRepository`/`resolve_prompt_reference`/`ModelRepository`/`RunRepository`/`TraceRepository`/`EvalRepository` 仓储读写 + `DbApprovalStore`/`LubanCheckpointer`（`app/factory.py` 装配审批落库与 Run checkpoint），API 路由自身无独立表 |
| `lubanagent.runtime` | `runs.py::create_run/resume_run/cancel_run` | `AgentRuntime.stream()`/`.stream_resume()`/`.cancel()`、`RunRequest`、`sse_stream()` 格式化 |
| `lubanagent.extension` | `deps.py::AppContext.registry`、`auth.py::get_principal`、`factory.py::discover` | `Registry`（工具/图/钩子/鉴权/观测扩展点查询）、`AuthProvider`/`AuthRequest`/`Principal` 类型、`discover()` 扫描用户扩展包 |
| `lubanagent.tool` | `deps.py::ApprovalManagerDep`、`tools.py::debug_tool` | `ApprovalManager`（审批状态）、`ToolResult`、`debug_tool()` |
| `lubanagent.observability` | `factory.py::resolve_backend` | `SafeBackend`/`db_failure_sink`/`resolve_backend()` 装配观测后端 |
| `lubanagent.config` | `deps.py::AppContext.settings`、`cli/main.py` | `Settings`/`get_settings()`/`configure_logging()`；CLI 侧读 `DATABASE_URL` 等环境变量 |
| `lubanagent.core` | 全模块 | `ErrorCode`/`LubanError`/`AgentDefinition`/`AgentVersion`/`PromptDefinition`/`PromptVersion`/`Run`/`Trace`/`TraceEvent`/`ToolDescriptor`/`BrainProfile`/`ModelCapability` 等领域模型直接复用为 API schema |
| `lubanagent.model` | `cli/main.py::_eval_run_async` | `BrainResolver`/`create_chat_model`（评测 judge 模型解析） |
| `lubanagent.observability.evals` | `cli/main.py` | `EvalDataset`/`EvalReport`/`EvalRunner`/`LlmJudge`/`compare_reports`/`load_dataset`（领域模型本体已下沉 `lubanagent.core.evaluation`，本路径为 re-export 垫片，`core/evaluation.py:1-7`） |
| 仓库根 `alembic.ini` + `migrations/` | `cli/main.py::migrate` | Alembic 迁移脚本位置与配置 |
| `lubanagent/ui/dist`（构建产物） | `factory.py::create_app` | 前端 SPA 静态资源目录是否存在，决定是否挂载 `StaticFiles` |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `app/main.py`（用户装配根） | 调用 `lubanagent.app.factory.create_app(extra_routers=(teams_router,), ui_dist=...)` | 在静态挂载前注册受保护团队路由；挂 `app.state.skill_registry` 和 `cs_team`，框架不反向 import `app`（`app/main.py:55-78`） |
| `lubanagent/ui`（前端工作台） | 全部 `/v1/*` 接口 + SSE `/v1/agents/{key}/runs` 与 `/v1/runs/{run_id}/resume` | 前端经 REST 拉取 Agent/Model/Tool/Trace/Approval/评测报告数据（评测页读 `GET /v1/evals` 列表 + `GET /v1/evals/{result_id}` 展开明细，断言四类如实展示），经 SSE 消费 Run 流式事件；OpenAPI schema 供前端类型生成参考。其中 `/chat` 用户端聊天页为 React Router **并列路由**（不在 AppShell 布局壳内、无侧边栏），消费 `POST /v1/agents/{key}/runs`（SSE，body 带 `user_id: "chat-user"` 观测标签 + history 前端拼接）与 `POST /v1/approvals/{id}/decide`（页内审批，第一个消费该接口的页面） |
| `docker/Dockerfile` 启动脚本、`scripts/dev.sh` | `luban migrate && luban init [--fill-missing] && uvicorn app.main:create_app --factory` | 容器启动序列与本地一键启动依赖本模块 CLI 命令的幂等性假设（dev.sh 用 `--fill-missing` 只补缺失 Agent，H08） |
| 外部业务系统 | `/v1/agents/{key}/runs` 等全部 REST/SSE 接口 | 业务系统作为独立进程通过标准 HTTP 调用集成，无需依赖 Python 包导入（架构硬约束：核心不依赖渠道层，业务系统走 REST/SSE） |
