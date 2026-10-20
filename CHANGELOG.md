# Changelog

> 本文件按领地分类记录变更（架构设计 §4.4）。标签含义：
>
> - `[framework]` —— 框架领地（`lubanagent/`）变更，用户直接合并即可。
> - `[scaffold-seed]` —— 脚手架种子（`app/` 内初始文件）变更，附"保留用户改动"合并指令。
> - `[shared]` —— 共享领地（`demos/` `tests/` `docs/` `docker/` `scripts/` `migrations/` 等）变更，按需合并。
> - `[migration]` —— DB schema 变更，附 `alembic upgrade` 命令与回滚步骤。
> - `[breaking]` —— 扩展点 Protocol 或 `core` 契约不兼容变更，升 major 并在 UPGRADE.md 给迁移示例。

## [1.0.0] - 2026-10-20 · 正式开源

**LubanAgent 首个公开版本**：开源（Apache-2.0）的 Agent 工程基座。Python ≥3.11 + LangChain + LangGraph，MySQL 8（开发期 SQLite 免安装），内置智能客服作为首个完整验证场景。克隆仓库、配一个模型，第一天就能写自己的业务 Agent。

### 核心能力

- **Agent 定义入库版本化**：人设 / 工具 / 规矩是一份存库、版本化的定义，改配置不发版，运行期生效；YAML 仅用于初始化导入与导出迁移。
- **模型工厂**：fast / smart / cheap / vision 四个逻辑槽位解耦 Agent 与具体模型，OpenAI 兼容协议优先（DeepSeek / Qwen / GLM / vLLM / Ollama 一个客户端全覆盖），换模型 = 改库映射，不改代码不发版。
- **治理层**：步数 / 预算 / 超时 / 取消熔断，Token 账本与费用归因（Run 树成本报表），Run / Trace 全程留痕可回放；确定性规则（权限 / 金额 / 审批）由代码执行，不靠 Prompt 声明。
- **一个引擎两种用法 + 多 Agent 编排**：日常 Agent = prebuilt + 治理包装（不写图）；确定性流程 = 手写 LangGraph 图；多 Agent = supervisor 委派（指定入口成员 / 扇出并行 / 防乒乓上限）——三者同库同治理，编排 Run 是会话 Run 的子 Run（预算继承、取消传播）。
- **可恢复运行**：普通 Agent Run 逐步 checkpoint，崩溃后通用 resume API 续跑（预算不重复放水、Trace 连续）。
- **审批持久化**：人工审批决定落库、跨重启可审计、并发裁决原子。
- **知识与记忆**：RAG 知识库（切块 / 守门 / 可不答不硬编）、摘要记忆、长期记忆（容量上限可配）。
- **Tool / Skill / MCP**：工具审批分级（高风险走人工审批流）、Skill 能力包沙箱安装与资源限额、MCP 长连接复用与出错自愈。
- **评测体系**：数据集 / 断言（输出 / 工具 / judge 评分 / 拒答）/ 录制重放 / A/B 对比，完整报告落库可查。
- **可插拔观测**：`ObservabilityBackend` 协议，默认 Langfuse 自部署（保数据主权），可选 LangSmith 或关闭；内部 DB 始终是 source of truth（双写 + 外发失败自动重试）。
- **开发者工作台**：Agents / Prompt 库 / 模型 / 工具 / Skills / 知识库 / 流程图 / Traces / 评测 9 个管理页面 + 用户端聊天入口（/chat），开箱即用。

### 工程交付

- **一键全栈（Docker）**：`docker compose up -d --build` 起 app + MySQL + Langfuse + mock-biz 演示业务服务，UI 与 API 同端口托管。
- **文档站**：8+1 课新手教程（图书馆借阅助手主线，零密钥可跑通链路）+ 字典式使用手册（功能 14 页 / API 参考 3 页 / 改造指南 8 页），VitePress 构建、GitHub Pages 自动发布。
- **质量底线**：586 后端测试 + 70 前端测试全绿（零外部密钥可跑）；依赖冻结（uv.lock / npm lock）+ pip-audit / npm audit CI 门槛 + OpenAPI 快照契约 + 领地守卫（import-linter + `.lubanagent-upstream`）。
- **内置方法论**：[spec-coding](.claude/skills/spec-coding/) skill——规格驱动开发闭环（意图 → 契约 → 编码 → 独立严审），克隆即用、语言无关。
- **领地契约**：`lubanagent/`（框架）与 `app/`（用户）三重隔离，跟随上游升级不丢你的改动，详见 [docs/UPGRADE.md](docs/UPGRADE.md)。

### 已知边界（如实声明）

- IM / 电商等渠道接入属商用扩展层，不在本仓库。
- 零密钥模式只验证链路（FakeLLM 固定台词），完整智力体验需接真实模型。
- 审批记录持久化 ≠ Run 可恢复（Run 恢复依赖 checkpoint，已默认启用）。

---

## [Unreleased]

### [framework]

- **普通 Agent Run 逐步 checkpoint + 通用 resume API（缺口 #2）**（`persistence/checkpoint.py` 新（自 `graph/checkpoint.py` 下沉，旧路径保留 re-export 垫片零破坏）+ `persistence/__init__.py` + `persistence/repositories/run.py` + `runtime/agent_loop.py` + `runtime/sse.py` + `observability/trace/writer.py` + `core/trace.py` + `api/routes/runs.py` + `api/routes/approvals.py` + `app/factory.py` + `cli/main.py`）：① `AgentRuntime` 新增 `checkpointer` 注入——非 None 时 prebuilt agent 以 `create_react_agent(..., checkpointer=...)` 编译、config 带 `thread_id=run-{run_id}`，LangGraph 在每个节点边界把执行快照落既有 `checkpoints` 表（**零表迁移**，表由 0001 建立）；未注入时零 checkpoint 写入。② 新增 `AgentRuntime.stream_resume(run_id)`：per-run `asyncio.Lock` 进程内串行，锁内五项校验（Run 存在 / 非终态 / 非本进程活跃 / 非子 Run / 存在 checkpoint）；版本快照钉死、大脑槽位按现状重新解析并如实回写 `run.model_key`；`RunRepository.run_usage`（新，token_usages/cost_records 账本合计）重建 `BudgetGuard`——崩溃续跑预算不重复放水；null-resume 续跑，事件协议与 `stream()` 一致。③ `TraceWriter.resume_run`：`runs` 状态置回 RUNNING、同一 trace 行追加 `RUN_RESUMED` 事件（SSE 映射 `run.resumed`，`SSE_EVENT_MAP` 14→15 条）——trace 连续、不重发 `run.started`。④ HTTP：`POST /v1/runs/{run_id}/resume`（SSE，帧格式同创建 Run；校验失败在流开始前抛错：Run/checkpoint 缺失 404，终态/活跃/子 Run 422，未启用 checkpoint 500 `CONFIG_ERROR`）。⑤ 装配：`create_app(checkpointer=...)` 哨兵语义——缺省 `LubanCheckpointer(db)`（**默认启用**，行为变化：普通 Run 开始落 checkpoint；不想要则显式传 None，此时 resume 返回 CONFIG_ERROR）；`luban eval run` 独立进程同语义装配（仅本进程可续跑）。⑥ 附带修复：`POST /v1/approvals/{id}/decide` 补齐 `await`（持久化 store 下 decide 此前静默失效），`decided_by` 取已验证的 `Principal.subject`。合并指令：`[framework]` 正常合并；默认启用 checkpoint 的行为变化见 `docs/UPGRADE.md` 第 5 节。

- **开源加固批次（H00-H14）：安全与治理收紧**（多模块，逐单元契约与验收记录见 `docs/plans/2026-10-10-open-source-hardening.md`）：①Skill 安装/删除目录隔离与原子替换（Zip Slip/符号链接拒绝、失败回滚旧包）；②统一受保护路由注册入口（`extra_routers` 绑定鉴权）消除静态挂载遮蔽；③MCP 导出白名单默认空、调用统一 `ToolExecutor`（高风险无审批器显式拒绝）；④上传/解压/解析/脚本输出资源边界（ZIP/DOCX 成员数与解压总量核验、解析子进程可终止限额、双流有界捕获）；⑤可信主体限流（Principal 分桶 + TTL/容量上限，匿名不信任任意 Bearer）与审批归因取经鉴权身份（客户端 `decided_by` 不采信）；⑥模型 `params` 白名单防密钥落库/回显（`[REDACTED]` 占位拒绝、脏数据只出安全副本）；⑦共享脱敏组件 `core/redact.py`——Trace 落库/SSE/观测外发边界全接线、非 LubanError 错误消息收敛为类型名；⑧演示部署收紧（`.dockerignore` 递归排除秘密与运行数据、compose 端口 loopback、种子只填不存在 + `--fill-missing`）；⑨能力件装配兑现（skill/摘要记忆/长期记忆/MCP 客户端注入点，缺依赖显式 `CONFIG_ERROR` 不静默看似开启）；⑩模型事件 schema 钉死 + 用量账本可空化（0009 迁移，未知不冒充零消费）+ safe/live 双模重放（脱敏记录显式拒绝）；⑪预算按当次单价计费 + 本地归因 + Run 树成本报表（own/descendants 不重复相加）；⑫`GraphRunner.resume` 终态拒绝 + 同线程串行锁；⑬工作台鉴权闭环（内存态凭据、REST/SSE/上传全请求附身份、401 可恢复）；⑭依赖冻结（uv.lock/npm lock + frozen 安装）、pip-audit/npm audit CI 门槛、OpenAPI 快照契约、独立进程真实装配测试；⑮测试环境隔离基建（合成 dotenv/端口/外网请求确定性拒绝）。合并指令：`[framework]`/`[shared]` 正常合并；默认收紧项（MCP 导出、模型 params、审批归因、种子覆盖语义、限流）见 `docs/UPGRADE.md` 对应迁移提示。

- **审批记录持久化（R11 遗留项：决定不随进程重启丢失、不重复、可审计）**（`core/tool.py` + `tool/approval.py` 重写 + `persistence/models/approval.py` 新 + `persistence/repositories/approval.py` 新 + `app/factory.py` + `runtime/agent_loop.py` + `api/routes/approvals.py` + `core/__init__.py`/`tool/__init__.py`/`persistence/__init__.py`）：审批存储抽为 `ApprovalStore` 协议（`save/get/list_pending/list_all/try_resolve` 全 async）——缺省 `MemoryApprovalStore`（进程内 dict，语义与旧版一致，跨实例 decide/wait 显式报错不静默放行）；`DbApprovalStore` 落库（每操作独立短 session），`create_app` 默认装配 `ApprovalManager(store=DbApprovalStore(db))`——API 服务审批即落库，`ApprovalManager()` 缺省仍内存 store（库内嵌/测试零迁移）。`decide` 经 `try_resolve` 条件更新（`UPDATE ... WHERE status='pending'`）原子裁决：并发/跨进程双 decide 恰一个成功，后到者 `VALIDATION_ERROR`；`wait_for_decision` 同进程 decide 走 `asyncio.Future` 即时唤醒、跨进程 decide 经周期轮询 store 兜底（默认 2s 可注入），总超时置 expired。`AutoApproveManager` 请求即批语义不变，注入持久化 store 时自动批准同样落库（`decided_by="auto-approve"`）。**接口形态变化（Python API 消费方注意）**：`ApprovalRequest` 从 `lubanagent.tool` 下沉 `lubanagent.core`（tool 兼容 re-export），新增 `resolved()` 方法；`ApprovalManager.create_request/decide/get/pending` 变 async、`decide` 返回终结态 `ApprovalRequest`（原先返回 None）、新增 `list_all`。**HTTP 契约不变**（GET /v1/approvals 支持 run_id 过滤；POST decide 404 语义不变），持久化装配下 GET 列表跨重启仍可见。**边界（如实声明）**：审批记录持久化 ≠ Run 可恢复——Run 无 checkpoint，进程重启后 Run 仍卡 `WAITING_APPROVAL`；跨进程 decide 感知延迟上限 ≈ 轮询间隔（默认 2s）。配套测试：`tests/lubanagent/tool/test_approval.py` 重写（跨实例恢复、decide 竞态原子性、审计回查、进程局部默认回归）、`tests/lubanagent/api/test_approvals.py` await 适配。

### [migration]

- **`0010_approval_records`（approvals 审批记录表）**：新表 `approvals`（id/run_id/tool_key/args/status/decided_by/decided_at/created_at，`run_id` 与 `status` 建索引），revision `f5a6b7c8d9e0`，down_revision `e4f5a6b7c8d9`，支撑审批决定落库（R11）。升级：`luban migrate`；回滚：`alembic downgrade e4f5a6b7c8d9`（删表，存量审批记录随之丢失）。

### [shared]

- **根目录瘦身：`UPGRADE.md`、`README.en.md` 迁入 `docs/`**（`docs/UPGRADE.md` + `docs/README.en.md`，原根路径删除）：两者均非 GitHub 根目录自动识别文件（根目录保留 README/LICENSE/CONTRIBUTING/SECURITY/CHANGELOG 五个约定文件），迁入后根目录收敛为「仓库门面 + 工具链配置」；`.lubanagent-upstream` `[shared]` 清单同步移除两条（`docs/` 目录已覆盖），README.md、README.en、website 手册/教程的指向与绝对链接、specs 资产登记同步更新。合并指令：shared 正常合并；若你在本地书签/脚本里硬编码过根路径 `UPGRADE.md` 或 `README.en.md`，改为 `docs/` 前缀。

- **用户文档体系重构：教程 + 使用手册随 VitePress 文档站独立部署**（`website/` 新增 + `docs/tutorial/` 迁移 + `docs/product-specs/LubanAgent-user-guide.md` 改跳转壳）：`docs/` 收敛为纯开发文档（设计/规格/契约，服务贡献者与 AI 协作工具）；面向用户的两套文档迁入 `website/`——`website/tutorial/`（8+1 课，新增第 9 课毕业设计与验收；每课加深：思考题钩子/架构视角/思考题与练习分列）、`website/manual/`（使用手册：快速开始/项目架构四图/功能手册 14 页/API 参考 3 页（REST 全表 + SSE 事件与错误码 + CLI）/改造指南 8 页/术语表）。站点 VitePress + mermaid，`npm run build` 构建、`.github/workflows/docs.yml` 部署 GitHub Pages（`DOCS_BASE` 支持项目页子路径）。根 README/README.en/AGENTS.md/CLAUDE.md/demos README/specs 资产登记同步改指向；`app/knowledge_sources/project_docs.py` 知识源清单改用新手册路径。合并指令：若你改过 `docs/tutorial/` 下文件，合并后内容在 `website/tutorial/`；旧用户手册链接（`docs/product-specs/LubanAgent-user-guide.md`）由跳转壳兼容，建议自行替换为新手册路径。

- **开源发布收尾（H15）：守卫补强 + 文档承诺与实测对齐**（`scripts/check_upstream_guard.py` + `.lubanagent-upstream` + `tests/test_upstream_guard.py` 新增）：①守卫新增 `--include-worktree` 本地模式——除提交 diff 外额外纳入未提交 tracked 修改与未跟踪新用户路径（CI 缺省仍只审提交 diff，语义不变）；②清单收紧：`app/config/` 下除种子样本（`.env.example`）与 `refund_policy.py`（补入 scaffold-seed）外全部 user-owned（原仅登记 `settings.local.py` 一个具体文件，用户新建 `app/config/*.py` 不受保护）；`[shared]` 补登根目录上游维护文件（README.en/SECURITY/CONTRIBUTING/LICENSE/uv.lock/.dockerignore）；③新增守卫测试 18 项（临时仓库正/反例、工作树模式、种子例外不误拒、glob 语义）；④文档承诺收敛：去除无依据百分比（80%/95%/90%/100%）与「零冲突/零漂移/绝不错答」等绝对承诺，改为以 CI 拦截机制描述（守卫降低冲突，不承诺任意 git merge 零冲突；RAG 守门口径改「可不答、不硬编」）。合并指令：shared 正常合并；若你在 `app/config/` 下有自己的 py 文件，合并后确认它是有意保留的（守卫此后拦截上游触碰）。

### [framework]

- **指令含 JSON 花括号字面量炸掉整个 Run（R13：多 Agent 团队首发即全挂）**（`runtime/context_builder.py`）：变量渲染原用 `str.format_map`，把指令里的 JSON 输出格式样例（supervisor 路由指令的刚需，内置 cs-triage 即含）当模板字段解析，`Invalid format specifier` 直接 ValueError 打死 Run；既有测试用短指令造 Agent，真实 YAML 从未跑过团队链路（盲区）。修复：占位符改精确正则替换（整个花括号内恰为一个标识符且与已声明变量同名才渲染），JSON 字面量/未声明占位符原样保留；不再支持 format_map 格式规格语法 `{name:>10}`（prompt 变量场景无使用）。3 个回归测试，端到端验证 cs-team 路由恢复。
- **`luban eval run` 独立进程三件套修复（R12：旗舰评测命令首次真正跑通）**（`cli/main.py` + `extension/decorators.py` + `extension/__init__.py` + `app/factory.py` + `runtime/knowledge_bootstrap.py` 新 + `runtime/__init__.py` + `knowledge/store.py` + `tool/approval.py` + `tool/__init__.py`）：此前 eval 在独立 CLI 进程构建 Runtime，缺用户领地装配——① 不扫描 `EXTENSION_PACKAGES`（带工具的 Agent 100% 报「工具已注销」）；② 无知识源连接器（带 knowledge 的 Agent 100% CONFIG_ERROR「知识源未注册」）；③ 无审批 manager（审批型工具执行被闸门拦截）。修复：① `discover_packages` 容错扫描助手（factory 与 CLI 共用，顶层包不存在记 warning 跳过）；② `restore_knowledge_sources` 知识源自举（runtime 层导出，按库中已有 chunk 重建通用 `SqlKnowledgeConnector`——种子源/托管源检索行为一致，已在注册表的自定义 connector 不覆盖；`KnowledgeStore` 新增 `list_sources`）；③ `--auto-approve` 选项 + `AutoApproveManager`（请求即批、Trace 事件流不变；缺省不配 manager——断言按「调用意图」判定（TOOL_CALL_STARTED），审批型工具不执行，断言依赖执行结果的场景须显式开启，注意高风险工具会被真实执行）。**语义提醒**：`expect_tools` 判定的是模型是否决定调用，不是执行成功。
- **评测体系打通（R5）：/v1/evals 读真实数据 + 完整报告落库 + 评测领域对象化**（`core/evaluation.py` 新 + `observability/evals/` 三文件改 re-export 垫片 + `persistence/repositories/eval.py` + `api/routes/evals.py` + `api/schemas.py` + `cli/main.py` + `core/errors.py`/`api/errors.py` + `ui/`）：评测从三张皮（内存 JSON / DB 单一 score / API 空壳 stub）收敛为一条链。`EvalCase`/`EvalReport` 等领域模型自 `observability/evals` 下沉 `core/evaluation.py`（persistence 仓储需要领域对象承载，分层契约禁止 persistence → observability），旧导入路径 re-export 垫片零破坏；新增 `EvalReportSummary`/`EvalReportDetail` 视图模型与 `EvalReport.model_key` 字段（CLI 落库前回填实际命中模型）。`EvalRepository` 从 dict 改领域对象（`create_dataset`/`get_dataset`/`save_report`→行 id/`get_detail`/`list_summaries`），`report` 列存完整报告（逐 case 断言明细、judge 结论、run_id）+ 聚合列（pass_rate/passed/total/错答率）供列表免解析。`luban eval run` 跑完完整报告落库（`--save` JSON 输出保留；compare 仍走 JSON 文件链路——JSON 是可移植基线载体，DB 定位持久化展示）。API：`GET /v1/evals` 接真实列表（dataset_key/agent_key 过滤 + limit）、新增 `GET /v1/evals/{result_id}` 详情（404 新错误码 `EVAL_RESULT_NOT_FOUND`，枚举 27→28），**旧 `GET /v1/evals/results` stub 与 `EvalRunOut`/`EvalResultOut` stub schema 已删除**。工作台评测页读真数据（列表 + 展开明细 + 断言四类：输出/工具/judge 评分/拒答，如实展示）。
- **MCP 长连接复用（keeper 后台任务 + 出错自愈）+ McpServer 领域对象化**（`mcp/client.py` + `mcp/registry.py` + `core/mcp.py` 新 + `persistence/repositories/mcp.py`）：MCP client 从每次调用新建会话改为长连复用——首个调用建立会话（stdio 子进程 + initialize 握手或 http 建连）并缓存；连接生命周期由专属 keeper 后台任务持有（规避 anyio 取消作用域跨任务退出问题，FastAPI 每请求一个任务的场景下复用仍安全）；调用抛非 LubanError 异常（连接断/超时/取消）时废弃连接、下次调用重建自愈；同一 client 调用 `asyncio.Lock` 串行化；`close()` 显式释放。registry 按 key 缓存 client（长连跨多次 discover 复用）+ 新增 `aclose()` 释放全部。仓储从 dict 改为 `McpServer` 领域对象（`core/mcp.py`，DB 列零改动）。MCP 尚未接入 factory 装配（无对象可挂 shutdown 钩子），close/aclose 备好待接入。
- **多 Agent 路由策略演进：指定入口成员 + 扇出并行**（`multiagent/team.py` + `multiagent/supervisor.py` + `multiagent/state.py`）：团队路由从仅有委派上限演进为三项可声明能力（`route(max_delegations, entry_member, fanout)`，均可缺省 = 老团队零行为变化）。入口成员：用户消息先进指定成员（entry-member 节点）再回 supervisor 决策，计入委派上限（不可借入口绕过防 ping-pong），非登记成员构建期 `VALIDATION_ERROR`。扇出并行：supervisor 一次决策委派多成员（`{"action":"delegate","agents":[...]}`，去重保序），`asyncio.gather` 在 member 节点内合并（图 state 单通道无 reducer，原生并行边会互相覆盖）——预算共享同池累计、取消传播、Trace 父子与单成员委派同一条代码路径；未知成员/空列表降级 answer 不炸编排。配套测试 `tests/lubanagent/multiagent/test_routing_policy.py`。

### [migration]

- **`0008_eval_results_report_details`（eval_results 评测报告明细列）**：`score` → `pass_rate`（语义钉死为通过率）、`details` → `report`（完整 `EvalReport` 序列化），新增 `passed_cases`/`total_cases`/`wrong_answer_rate` 聚合列（server_default 0 兜底）；旧行数据保留（report 即原 details 的全量报告 JSON，聚合列历史行为 0，下次评测覆盖刷新）。升级：`luban migrate`；回滚：`alembic downgrade c1d2e3f4a5b6`（仅还原列名/删聚合列，不动存量数据）。

### [framework]

- **风险登记 R1-R10 集中清偿（第一批 8 项）**（多模块，详见各分项提交）：① `luban init` 内容未变不再追加版本（剔除 version/status 深度相等即跳过，输出标 unchanged）——杜绝 dev.sh 重启导致版本号膨胀；② 鉴权配置缺失（`auth_enabled=True` 且无 `api_key` 且无自定义 AuthProvider）改为装配期 fail fast（CONFIG_ERROR 含三条修复出路），不再「表面起来了但全站 500」；③ 图中断恢复接回父级预算池与取消传播链（interrupt 时冻结 guard/token 绑定，resume 按「显式传入>冻结绑定>兜底」重建；跨进程/父 Run 已终结时如实降级）；④ 观测外发失败自动重试闭环——export_failures 从只写不读到 drain 收尾自动回放（成功删记录、默认 3 次后放弃保留行），同步调用路径失败先入内存缓冲再落库不再静默丢失；⑤ Skill 沙箱真隔离——env 白名单透传（宿主密钥默认不可见）+ RLIMIT_CPU/AS/NPROC/FSIZE 资源限额（macOS 不支持项探测后跳过），网络仍可达等剩余风险 docstring 如实声明；⑥ 长期记忆每 agent+user 容量上限（默认 1000，env `LONG_TERM_MEMORY_CAPACITY`）超限淘汰最旧 + 知识检索 top_k 钳制 [1,100]（0/负数此前静默吞全部结果）；⑦ KV-cache 稳定前缀不变量从注释约定升级为测试硬守卫（前插动态内容/改写历史即红）；⑩ 后端适配器（Langfuse/LangSmith）新增 `replay` 方法支撑失败回放。

### [shared]

- **新手动手教程（`docs/tutorial/` 新增，8+1 课线性路径）**：与字典式用户手册构成「学/查」互补——第 0 课纯概念（发明重现式讲幻觉/Tool/RAG/评测/编排，可独立分享引流），第 1-8 课从零跑通到部署，贯穿主线为图书馆借阅助手 `my-lib-assistant`（与内置客服逐能力同构）。每课固定结构（学习目标/概念/动手含内联检查点/自检清单/常见坑/三级练习），检查点来自写作时真实执行；配套 `reference/` 参考产出 6 份。防漂移三层：新增 `docs/specs/modules/tutorial.md` 事实资产登记（纳入漂移抽查）、与手册互链（手册 §0/§11/§14 加「新手先做教程第 N 课」反向链接）、主 README 中英双语加「两条路径」入口（快速开始之前）；demos README 文首挂教程指引。零密钥口径统一诚实化：零密钥只验链路（FakeLLM 固定台词），完整体验需真模型（README/demo README/教程三处同口径）。合并指令：全部为 docs/ 新增文件与文档头部链接行，无代码改动，正常合并即可。

### [scaffold-seed]

- **种子评测集与客服人设锚点修复（真模型实测，R12 连带）**（`app/datasets/cs_basic.yaml` + `app/agents/customer_service.yaml`）：eval 修通后首次真模型端到端验证暴露两处种子漂移——① `refund_large_approval` 用例单轮输入未带退款原因，人设升级后模型按 SOP 先询问原因，到不了 `refund_apply`（输入补全原因）；② 人设「先安抚情绪」锚点在 deepseek-chat 上不成立（回复直奔查单/结果），加硬性铁律（用户带不满情绪时回复第一句必须共情/致歉）与「信息齐全直接提交退款不反问」规则。实测 cs_basic 5/5（judge 用例存在评分方差）、cs_knowledge 2/2。合并指令：若你已原地改过这两个文件，用 `git merge -X ours` 保留你的版本，再手动比对锚点。
- **退款审批阈值单点配置**（`app/config/refund_policy.py` 新）：500 元审批口径此前硬编码在 Tool 描述、Agent 指令 YAML、图分支三处——收敛为单一常量，Tool 与 Graph import（渲染文本与判定语义逐字不变）；YAML 指令为静态文本无法 import，守卫测试 `tests/demos/customer_service/test_refund_policy.py` 校验 YAML 数字与常量一致，改常量漏改 YAML 即测试红。合并指令：`app/config/refund_policy.py` 为新文件（已列入 `[scaffold-seed]`）；若你已原地改过 `refund.py`/`refund_flow.py`，用 `git merge -X ours` 保留你的版本，再手动把两处 500.0 换成 `REFUND_APPROVAL_THRESHOLD`。

### [migration]

- **`0007_export_failures_retry_count`（外发失败重试计数列）**：`export_failures` 加 `retry_count`（default 0），支撑重试消费端的计次与放弃判定。升级：`luban migrate`；回滚：`alembic downgrade b8c9d0e1f2a3`（仅删列，不动存量数据）。

### [framework]

- **`luban init` 槽位播种只填空位（修：重启打回假模型）**（`app/lifespan.py` + `persistence/repositories/model.py`）：`seed_defaults` 此前对四槽位映射无条件 upsert，`dev.sh` 每次重启都跑 `luban init`，把用户经工作台/API 配好的真实模型（如 deepseek-chat）覆盖回 `fake-chat`——表现为「聊天全是『你好，我是 LubanAgent。』固定台词」。现在已存在的映射行不覆盖、只播种空槽位（空库首次 init 行为不变）；模型定义 upsert 语义保持。`ModelRepository` 新增 `has_brain_mapping`。配套回归测试 `test_init_preserves_existing_brain_mappings`。

### [framework]

- **聊天页形象改左侧陪伴栏 + 3D 说话动效**（`ui/` + 用户文档）：/chat 虚拟形象从页头小图改为左侧大号陪伴区（224px，窄屏自动收起）——"Agent 站在聊天旁陪你聊"而非页角小图标。`AgentAvatar` 新增 `speaking` 属性：流式回复期间播放 GLB 内嵌 `talk` 动画（嘴巴开合循环），回复结束 pause + currentTime 归零停回闭嘴帧（v4 model-viewer 元素级 API 无 `stop()`，此前调用被静默吞错导致动画不停）。内置客服示例形象更新为脚本生成的「小鲁」3D 机器人 GLB（12 部件、内嵌 talk 动画，35KB 零外部依赖资产）。

### [framework]

- **Agent 虚拟形象（avatar）：上传 SVG/PNG/3D + 聊天页展示**（`lubanagent/avatar/` 新模块 + `api/routes/avatars.py` 新 + `core/definition.py` + `config/settings.py` + `app/factory.py` + `ui/`）：每个 Agent 可配一个展示形象。`AgentDefinition` 新增 `avatar: str | None`（落盘文件名，JSON 列零迁移）；三端点 `PUT/GET/DELETE /v1/agents/{key}/avatar`——PUT multipart 上传（svg/png/glb/gltf 白名单，≤ 20MB，`AvatarManager` 校验 + `data/avatars/{key}/avatar.{ext}` 每项一目录落盘，换类型自动清旧文件）并**服务端产生新版本**（上传即版本，照 Prompt 分配先例，单一原子操作）；GET 是本系统首个二进制响应端点（FileResponse + 按后缀 Content-Type + `CSP: default-src 'none'`/`nosniff` 安全头——用户上传 SVG 直接打开也不执行脚本）；DELETE 删文件并追加 `avatar: None` 版本。新错误码 `AVATAR_NOT_FOUND`→404（枚举 26→27）。工作台 Agent 编辑器「身份」区新增形象卡（预览 + 上传/移除，立即生效独立于表单草稿）；用户端聊天页 /chat 头部显示所选 Agent 形象——SVG/PNG 用 `<img>`，GLB/GLTF 用 `@google/model-viewer` **真 3D 渲染**（camera-controls 可拖拽查看、动态 import 不膨胀 bundle、不用 auto-rotate 保持确定性），无形象回退聊天图标。YAML shorthand 顶层键新增 `avatar`（导入导出对称）。纯展示字段，运行期逻辑零感知。

### [framework]

- **用户端聊天页 /chat（示例级入口）**（`ui/` 新增 `pages/chat/` + `lib/sse.ts` 扩展 + 路由/侧栏 + 依赖 react-markdown/remark-gfm）：此前要跟内置客服对话只能进管理端调试台，现在侧栏「用户端聊天」进入无侧边栏全屏聊天页。Agent 下拉选择（默认内置客服）、多轮流式对话（session 共用 + history 前端拼接 + `user_id: "chat-user"` 观测标签）、工具调用只显示状态行（执行中/完成/失败，不露参数）、回复终态后 markdown 渲染（流式期纯文本防半截语法抖动）；「新会话」换 session 清轮次，切换 Agent 自动重开会话。**页内审批**：`tool.approval.requested` 到达时气泡内出现批准/拒绝按钮（第一个消费 `POST /v1/approvals/{id}/decide` 的页面，`decided_by: "chat-user"`），大额退款审批在聊天中闭环。`useRunStream` 新增 `errorStatus`（非 2xx 状态码，401 时错误卡附鉴权提示）。**边界**：示例级入口——不做用户注册/登录/多租户/会话持久化；沿用工作台匿名访问假设（`AUTH_ENABLED=true` 时 401，演示栈 false）；生产用户端由业务系统自建走 REST/SSE。路由表 13→14（/chat 为并列路由，不在 AppShell 布局壳内）；app.test 路由断言补齐 /chat 与历史欠账 /graphs。配套：YAML shorthand 顶层键新增 `greeting`（导入导出对称，客服示例 Agent 补问候语——/chat 空会话开箱即有问候）。

- **图只读结构视图（API + 工作台「流程图」页）**（`graph/builder.py` + `api/routes/graphs.py` 新 + `api/schemas.py` + `core/errors.py` + `ui/`）：确定性流程图此前只能读源码才知道长什么样，现可在工作台直接查看拓扑。`GraphBuilder` 新增 `structure()` 只读访问器（返回 `GraphStructure` 快照：节点 kind/agent_key/prompt、直边、条件边、entry；不校验不编译，可执行体不暴露）；新增 `GET /v1/graphs`（列表，`structure_available` 标记 DSL 可视性——builder 返回非 GraphBuilder 的图标"无结构视图"不炸）与 `GET /v1/graphs/{name}`（拓扑详情，请求时实时调 builder 读结构，不缓存不持久化、不触发 LangGraph 编译）。新错误码 `GRAPH_NOT_FOUND`→404（registry 内部 VALIDATION_ERROR 路由层转 404）、`GRAPH_STRUCTURE_UNAVAILABLE`→422。工作台新增「流程图」页（12→13 路由）：左图列表 + 右 BFS 分层拓扑渲染，节点按类型着色（step 蓝/agent 紫/human 橙）、条件边虚线双出边、START/END 标记，纯确定性布局无图形库依赖。**只读定位**：编辑图仍走代码（`app/graphs/`，保持流程可测试可审计），不做可视化设计器。

### [shared]

- **Docker 交付真正带上工作台 UI**（`docker/Dockerfile` + `.dockerignore` + `README.md` + `README.en.md` 新）：镜像改三阶段构建——node:22 打工作台前端（npm ci + vite build）→ venv → 运行时把 dist 带进 `lubanagent/ui/dist`，由 FastAPI 同端口 8000 托管（`factory.py` ui_dist 逻辑，此前镜像无前端、README 宣传 5173 名不副实）。`.dockerignore` 语义更新（UI 在镜像内构建，本地产物不进镜像）。README 修正 Docker 段宣传（工作台+API 同端口 8000）；新增英文 README（`README.en.md`）与顶部英文简介互链；quickstart 补前置依赖声明（uv / Node ≥20）与首次对话 curl 验证示例（`customer-service-bot`，FakeLLM 兜底零密钥可跑）。修正既有失实表述：「多租户鉴权」改为「API Key 鉴权」（代码无 tenant 概念，多租户隔离由业务网关负责）。
- **开源社区基建**（新 `CONTRIBUTING.md` + `SECURITY.md` + `.github/ISSUE_TEMPLATE/`）：贡献指南（领地范围、CI 自查清单、架构硬约束、PR 流程）、安全策略（漏洞私报流程、部署侧安全清单、设计侧边界如实声明）、bug/feature 两套 issue 模板。
- **文档路径漂移修正**（`docs/contracts/D1-9-ci.md` + `docs/specs/modules/api-app-cli.md`）：`.dockerignore` 引用位置改为仓库根（build context 是仓库根）；Dockerfile 行号引用 26→39 对齐三阶段改造后的新行号。

### [framework]

- **工作台知识库管理（多格式上传 + 切块配置 + 检索测试）**（`knowledge/manager.py` 新 + `knowledge/ingest.py` + `api/routes/knowledge.py` 新 + `app/factory.py` + `ui/`）：知识源管理从"写死代码清单重启灌库"升级为工作台页面操作。新增 `KnowledgeSourceManager`（托管源 CRUD/重切/恢复注册）与 `/v1/knowledge/*` 六端点（sources 列表/上传创建/删除/重切/切块查看/检索测试；重名 409、非托管源删除 422、新错误码 `KNOWLEDGE_SOURCE_NOT_FOUND`→404）。文档解析扩到八种格式（md/txt/pdf/docx/csv/json/html/xml：pdf 经 pypdf、docx 经 python-docx 可选依赖，csv/json 行式文本化，html/xml stdlib 剥标签）；切块大小与重叠按知识源独立配置，改参数一键从落盘文件重切（上传文件持久化在 `knowledge_files_root`（默认 `data/knowledge`，env `KNOWLEDGE_FILES_ROOT`）每源一目录，重切不用重新上传）。上传文件限 10MB/50MB，文件名 basename 净化 + 目录 resolve 校验双保险防路径穿越。页面建的知识源重启后自动恢复注册（factory lifespan `ensure_restored()`），Agent YAML `knowledge.sources` 直接引用名字即可；代码种子源（project-docs/product-faq）在列表标「内置」、不可删，行为零变化。装配收敛：embedder 选择逻辑从 `app/main.py` 上移到工厂（`app.state.knowledge_store/embedder/manager` 单实例共用，种子源灌库与托管源共用向量空间）；`ingest_file` 保持签名不变（种子源零改动）。import-linter knowledge 契约补 `lubanagent.config` 禁止项。工作台新增「知识库」页（上传/切块详情/重新切块/检索测试面板——现场试查命中块与 score，配 Agent 防错答阈值的直接工具），路由表 11→12 页。新增 optional extra `docs = [pypdf, python-docx]`。


- **FakeEmbedding 检索质量改进（2048 维 + 高频功能字过滤）**（`knowledge/embedding.py`）：默认 dim 512→2048，并新增 `_STOPWORDS` 单字功能字滤除。两个去噪设计：① 512 维在文档级语料（数百 chunk × 百级 token，实测 484 chunks）下桶饱和，无关查询 cosine 虚高到 0.3+；② "怎么/什么/吗"类高频功能字在任何中文语料都命中，抬高口语化查询与无关文档的 cosine。实测（五份项目文档语料）：正例查询 0.24-0.42、无关查询 0.13-0.235（上界 < 阈值 0.24），`score_threshold` 守门阈值可校准。确定性语义不变（同文本同向量）；存量 DB 旧维度向量需重灌——app 启动 `on_startup` 钩子自动 replace 重灌，无需手工操作。

- **RAG 防错答三层防线（可不答、不硬编）**（`core/definition.py` + `runtime/capabilities.py` + `runtime/agent_loop.py` + `knowledge/` + `observability/evals/` + `cli/yaml_io.py` + `config/settings.py` + `ui/`）：`KnowledgeConfig` 新增守门三字段 `score_threshold`（cosine 低于阈值的检索片段丢弃，0=不过滤）、`no_hit_fallback`（过滤后全 miss 的兜底话术，配置后代码强制短路拒答——不调模型零 token，Run 仍 COMPLETED，end_reason=`knowledge_no_hit`，SSE 事件 `knowledge.no_hit`）、`max_age_days`（按灌入时自动打的 `metadata.ingested_at` 丢弃超龄 chunk，无时间戳的存量数据保留），全部带默认值、存量 Agent 零行为变化。`cite=True` 落地强引用约束（只依据编号片段作答、答不出直接说不知道、`[n]` 编号引用），`cite=False` 纯片段罗列。评测新增拒答硬指标：`expect_refusal` 用例须事件流含 `knowledge_no_hit`（确定性判定不依赖 judge，未拒答即错答），报告新增 `wrong_answer_rate`（目标 0）。YAML `knowledge` 段支持 dict 全字段形态（未知键报错），导出按"仅 sources 非空且其余默认→list，否则 dict"往返兼容。`Settings` 新增 `embedding_model`/`embedding_base_url`/`embedding_api_key_env`（配了走 OpenAI 兼容 embedding，否则 FakeEmbedding）。`FakeEmbedding` 维度 64→512（64 维 hash 碰撞使无关查询 cosine 虚高到 0.3+，守门阈值无法校准；确定性语义不变，存量向量需重灌——app 启动自动 replace 重灌）。工作台 Agent 编辑器"记忆与知识"区新增三字段（阈值/兜底话术/时效）。无 DB schema 变更（metadata 是自由 JSON）。

- **Prompt 库一等实体化**（`core/prompt.py` + `persistence/models/prompt.py` + `repositories/prompt.py` + `api/routes/prompts.py` + `ui/`）：Prompt 成为独立版本化的一等实体（`prompts`/`prompt_versions` 表，与 agents 同型：指针 + 不可变快照）；Agent 定义新增双轨引用字段 `prompt_key`/`prompt_version`（均可空，空 = 纯内嵌，存量 Agent 与 YAML init 零行为变化）。引用解析在**落库前**：`resolve_prompt_reference` 取 Prompt 当前版（或钉住版）把全文写入 `instructions`、变量合并（Agent 自带覆盖同名），Agent 版本快照内嵌 Prompt 全文，运行期不回查 `prompts` 表（回放确定性）。语义：整份定义回传含旧 `prompt_version` 会钉住旧版（改其他字段发版不悄悄换 Prompt）；Prompt 新版不自动作用于引用方，Agent 吃新版须显式产生新 Agent 版本（`prompt_version: null` 取当前版）。新增 `/v1/prompts` 六端点（列表/创建/详情/版本列表/发版/版本详情，409 与 agents 路由同风格）与错误码 `PROMPT_NOT_FOUND`/`PROMPT_VERSION_NOT_FOUND`（404）。工作台新增「Prompt 库」页（列表/新建/编辑发版/版本历史/分配给 Agent/引用反查），Agent 编辑器指令 Tab 双轨（引用态只读预览 + 转内嵌），Agent 调试台「Prompt 试跑」Tab 支持"从 Prompt 库载入"。

- **Agent 调试台升级双 Tab 工作台，Playground 独立页移除并入**（`ui/`，后端零改动）：`/agents/{key}/debug` 从纯对话页升级为「对话 + Prompt 试跑」双 Tab 工作台——原独立 Playground 页（`/playground`）整体并入为「Prompt 试跑」Tab：围绕当前 Agent 并排试跑（左 = 库内定义基线，右 = 临时修改），改 variables 即时生效（请求体原生字段），改 instructions 经 `instructions_override` 临时生效（不入库），可从 Prompt 库载入；每次点击生成新 session_id（original/modified 共享，Trace 成对可查）。Tab 用 forceMount 切换不丢状态、后台 SSE 不断流；对话与试跑的 session_id 相互独立（连续会话 vs 无状态对比）。`/playground` 路由删除（旧链接重定向到 /agents），侧栏「Prompt 调试」项移除，页面数 12 → 11。

- **Skill zip 包上传安装与删除**（`api/routes/skills.py` + `skill/registry.py`）：`POST /v1/skills` 上传 zip 解压到 `skills_root/{name}/`（防 zip slip、name 限 `[A-Za-z0-9_.-]`、须恰好一个含 SKILL.md 的包、同名覆盖、10MB 上限），`DELETE /v1/skills/{key}` 删除；`GET /v1/skills` 改读内存视图（`SkillRegistry.list_packages`，不再依赖 DB 仓储）。新增 `Settings.skills_root`（缺省 `app/skills`）；`create_app` 新增 `on_startup` 钩子（`on_event("startup")` 会被 lifespan 覆盖不执行）。工作台 Skills 页配套上传/分配 UI（`ui/`）。

- **模型工厂计费字段隔离**（`model/openai_compat.py`）：`ModelDefinition.params` 同时承载「模型构造参数」（`temperature`/`max_tokens` 等，透传给 `ChatOpenAI`）与「框架计费字段」（`input_price_per_1k`/`output_price_per_1k`，由 `BudgetGuard` 读取）。修复前工厂把整个 `params` 透传给 `ChatOpenAI`，计费字段被 SDK 拒收致 `TypeError`，表现为「配了真实模型单价后一调就崩」。现构造前摘出 `_FRAMEWORK_RESERVED_PARAMS` 保留字段，构造参数与计费字段各走各的。验证：DeepSeek 真实调用跑通，费用按 `in/1000×input_price + out/1000×output_price` 正确归零非零。

### [scaffold-seed]

- **新增 4 个能力展示 Agent**（`app/agents/` 新增 4 个 YAML，均列入 `.lubanagent-upstream` `[scaffold-seed]`）：补齐主客服示例未覆盖的六项框架能力，每个 YAML 头部注释标明演示点——`cs-intake` 工单预分类器（`io_contract.output` JSON Schema 结构化输出 + `memory: none` 无状态 + cheap→fast 降级链）、`cs-notifier` 事件通知文案官（`io_contract.input: json` 系统事件输入，演示 Agent 服务于业务系统流水线而非终端用户）、`product-visual` 商品图片问询助手（`brain: vision` 槽位 + multimodal 输入 + RAG 防错答守门组合）、`cs-followup` 回访小结助手（`memory: session-summary` 会话摘要记忆 + `budget_cost: 0.5` 费用预算双熔断）。全部零配置可跑（FakeLLM 三能力全开，能力闸门验证通过），`luban init` 一并导入。

- **客服示例 Agent 人设专业化**（`app/agents/` 全部 5 个 YAML）：提示词从 2-3 行升级为结构化专业人设（角色/流程/工具规则/表达规范/边界升级，约 400-800 字），每个 Agent 有名字与职责边界——customer-service-bot「小鲁」（接住情绪→确认诉求→解决问题→交代结果四步服务流程）、cs-faq「小答」（只答检索片段覆盖的部分、数字逐字引用）、cs-refund「小退」（查证→算金额→提交的 SOP 顺序）、cs-triage「小调」（按优先级的分流规则 + 转述带全关键信息）、dept-project 小助手（决策约定逐字引用不加解读）。评测断言锚点全部保留（先安抚再处理、超范围转人工拒答、`[n]` 来源引用、>500 元审批口径三处一致、triage JSON 输出契约逐字不变）；cs-faq 补 `top_k: 5` 显式声明（等于默认值，零行为变化）。合并指令：若你已原地改过任一 Agent YAML，用 `git merge -X ours` 保留你的版本。

- **种子源装配改用工厂共享实例（`app/main.py`）**：embedder 选择与 store 构造上移到框架工厂，`app/main.py` 删除本地 `OpenAICompatibleEmbedding`/`FakeEmbedding` 二选一逻辑，改从 `app.state.knowledge_store`/`app.state.knowledge_embedder` 取同一实例灌 product-faq/project-docs 种子源（种子源行为零变化，仅装配收敛）。合并指令：若你已原地改过 `app/main.py`，用 `git merge -X ours` 保留你的版本，再把种子源三行（connector 注册 ×2 + on_startup 灌库）里 `store`/`embedder` 的来源改为 `app.state.knowledge_store`/`app.state.knowledge_embedder` 即可。

- **部门项目小助手范本（dept-project Agent + project-docs 知识源）**：新增 `app/agents/dept_project.yaml`（dept-project：纯 RAG 问答 + 防错答守门——`tools: []`、`brain: smart`、`score_threshold: 0.24`（新 embedder 对项目文档语料的实测分界，注释注明换 embedding 或扩文档需重测）+ `no_hit_fallback` 兜底话术）与 `app/knowledge_sources/project_docs.py`（五份核心文档清单固定在 `_DOC_FILES`，chunk 300/overlap 30，metadata 记 `doc` 来源文件名供 `[n]` 引用溯源 + `ingested_at`）；`app/main.py` 种子新增 project-docs 知识源装配与启动灌入（import + connector 注册 + ingest 调用三行）；`cs_faq.yaml` 守门注释同步新 embedder 实测数值（无关查询 ≈0.06、FAQ 命中 ≈0.5，阈值 0.2 不变仍成立）。换部门项目只需改 `_DOC_FILES` 清单与复制改写 YAML。合并指令：两个新文件与 `app/main.py` 三行装配均已列入 `.lubanagent-upstream` `[scaffold-seed]`——若你已原地改过 `app/main.py` 或 `cs_faq.yaml`，用 `git merge -X ours` 保留你的版本，再手动补上 project-docs 三行装配。

- **cs-faq 升级为防错答守门范本 + 新增守门评测集**：`app/agents/cs_faq.yaml` knowledge 段改 dict 形态（`score_threshold: 0.2` + `no_hit_fallback` 兜底话术；注释注明 0.2 是 FakeEmbedding 语料实测值、生产 embedding 需重调）；新增 `app/datasets/cs_knowledge.yaml`（纯 RAG 问答 Agent 的守门评测集：faq_hit 正例 + `expect_refusal: true` 拒答硬指标用例），`cs_basic.yaml` 的 unknown_reject 保持 judge（面向带工具 Agent 的模型层拒答，注释说明两者分工）。合并指令：若你已原地改过 `cs_faq.yaml`，用 `git merge -X ours` 保留你的版本；`cs_knowledge.yaml` 为新文件（已列入 `[scaffold-seed]`，upstream-guard 不拦截）无冲突。

### [migration]

- **`0006_knowledge_sources`（工作台知识源表）**：新建 `knowledge_sources`（name 唯一；description/chunk_size/overlap/files JSON/chunk_count/时间戳）一张表，只存工作台创建的托管源（代码种子源不入表）。升级：`luban migrate`（等价 `alembic upgrade head`）；回滚：`alembic downgrade a7b8c9d0e1f2`（仅删新表，不动 knowledge_chunks 存量数据）。
- **`0005_prompt_library`（Prompt 库表）**：新建 `prompts`（key 唯一 + `current_version` 指针）与 `prompt_versions`（`UniqueConstraint(prompt_id, version)` 不可变快照）两张表。升级：`luban migrate`（等价 `alembic upgrade head`）；回滚：`alembic downgrade f1a2b3c4d5e6`（仅删两张新表，不动存量数据）。

## [0.1.0] - 2026-07-27

首个发布版本：若依式 Agent 起步基座开源核心（F1-F13）全量交付。

### [framework]

- **核心基座**：Agent 定义层（DB 记录 + 库内版本化，YAML 仅 init/export）、fast/smart/cheap/vision 四槽位逻辑大脑（`model/brain_resolver.py`）、OpenAI 兼容协议模型工厂（DeepSeek/Qwen/GLM/vLLM/Ollama 一个客户端全覆盖）、FakeLLM 测试底座。
- **Agent Runtime**：统一跑在 LangGraph 上——普通 Agent = prebuilt agent + 治理包装（步数/预算/超时/取消/Trace），编排 Run 作为会话 Run 子 Run（预算继承、取消传播）。
- **Tool/MCP 治理**：`@tool` 注册、审批流（高风险工具人工确认）、Mock 模式、MCP client/server 双向接入。
- **观测与治理**：可插拔 `ObservabilityBackend`（默认 Langfuse 自部署，可选 LangSmith，可切 none；内部 DB 始终 source of truth 双写）、评测体系（三类断言 / LLM-as-judge / 回归 / 录制重放 / A/B 对比）、成本治理（事前 Token 估算 + 事中预算熔断 + 事后归因）。
- **扩展能力**：最小 RAG（`knowledge/`）、三种记忆策略（`memory/`）、Skill 运行时（兼容 Agent Skills 开放标准，`skill/`）、图编排（`graph/`，Graph DSL + checkpointer 落自有库 + 流程作 Tool）、多 Agent 协作（`multiagent/`，supervisor/子 Agent 委派拓扑，与单 Agent Run 同库同治理）。
- **接入与装配**：REST/SSE API（`/v1/agents/*/runs`、teams、runs 等）、可插拔鉴权、`create_app` 装配工厂、`luban` CLI（`init` / `migrate` / `eval` / `export`）。
- **开发者工作台**：`lubanagent/ui` 前端（11 页：Agent 列表/Agent 编辑/运行调试台/Playground/模型列表/模型对比/Tool/Skill/Trace 列表/Trace 详情/评测）。

### [scaffold-seed]

- 用户领地初始结构：`app/main.py` 装配根、`app/config/.env.example`、包标记 `__init__.py`。
- 客服示例种子：4 个 Agent YAML（customer-service-bot + triage/faq/refund 分流成员）、`query_order`/`refund_apply` 业务 Tool、`refund_flow` 退款流水线（大额人工审批）、`cs_team` 分流团队、`refund-sop` Skill、产品 FAQ 知识源、`cs_basic.yaml` 评测数据集、团队自定义路由。
- 合并指令：本次为首发，无历史改动需保留；后续版本若动种子文件，将在此给出 `git merge -X ours` 等保留指令。

### [shared]

- 全量测试基建：499 后端测试（FakeLLM + SQLite + noop 观测，零外部密钥）+ 40 前端测试。
- CI 守门（`.github/workflows/ci.yml`）：import-linter 依赖方向守卫（20 条契约）、ruff、alembic 三向迁移冒烟、pytest + 覆盖率门槛（核心子包 ≥85%、整体 ≥70%，`scripts/check_coverage.py` 分项断言）、CLI 冒烟、前端 typecheck/lint/test/build、MySQL service 实跑 alembic、`.lubanagent-upstream` 守卫（`scripts/check_upstream_guard.py`）。
- Docker 交付：多阶段非 root `docker/Dockerfile`、根 `docker-compose.yml`（app + MySQL 8 + Langfuse + langfuse-db + mock-biz 全栈演示形态）。
- 文档：PRD / 技术方案 / 架构设计 / 用户手册 / 开发计划五份定稿，`demos/customer_service/README.md` 30 分钟跑通指南。
- `.lubanagent-upstream` 上游追踪清单（framework / shared / scaffold-seed / user-owned 四节 glob）。
- Docker 构建优化：根 `.dockerignore` 裁剪构建上下文（排除 `.venv`/`node_modules`/`tests`/`docs`/`.git`）、app 容器 healthcheck（端口探活，不依赖鉴权）。

### [migration]

- Alembic 迁移链 `migrations/`（0001 → 0003）：Agent 定义/版本、模型与槽位映射、Run/Trace、审批、评测、知识库、Skill、图 checkpoint、多 Agent 团队等全部首版表结构。
- 升级命令：`luban migrate`（等价 `alembic upgrade head`）；回滚：`alembic downgrade base`（三向迁移冒烟已在 CI 固化）。
