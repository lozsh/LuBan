<!--
TEMPLATE-LOCK: 本文件章节锁定为以下清单，禁止新增/改名/调序：
1. 长期质量风险
2. 复盘记录（无则写"无"）
首次初始化时留空，不推测风险，不为凑数硬造条目。只登记长期有效的风险与真实复盘事件，一次性排查过程写任务目录 tasks.md，不写这里。
-->

# 质量风险与复盘 — LubanAgent

## 长期质量风险

<!-- 本节登记长期存在、需要持续关注的质量风险（技术债、脆弱依赖、已知缺陷等）。首次初始化时为空，请在发现风险时按需补充。 -->

| 编号 | 风险描述 | 影响范围 | 事实来源 | 状态 |
|---|---|---|---|---|
| R1 | KV-cache 友好的"稳定前缀 + append-only"不变量只是组装约定，无运行期断言强制；未来若在 system prompt 前缀插入动态内容，不会有测试或断言拦截 | runtime | `lubanagent/runtime/context_builder.py:1` 注释自述"尽力而为非硬约束" | 已修复（2026-09：测试级守卫 `tests/lubanagent/runtime/test_kv_cache_prefix_invariant.py`——期望前缀独立拼出不经组装器，前插/穿插/改写历史三种破坏方式均触发测试失败） |
| R2 | `GraphRunner.resume()` 硬编码 `parent_ctx=None`，interrupt 恢复执行时会丢失原始父 Run 的预算池/取消 token 绑定，只能退回 Runner 自身兜底预算 | graph、multiagent | `lubanagent/graph/runner.py:118` | 已修复（2026-09：interrupt 时按 thread_id 冻结 guard/token 引用进 runner 内存绑定表，resume 按「显式传入 > 冻结绑定 > 兜底」重建；跨进程重启/父 Run 已终结时如实降级，见 runner.py docstring） |
| R3 | `SafeBackend._emit_inline`（无运行中 event loop 的同步调用路径）失败只记日志、不写 `export_failures`，若生产环境存在非 async 调用路径触发 emit，会出现失败静默丢失、无留痕可查的盲区 | observability-adapters | `lubanagent/observability/adapters/base.py:114-128`，测试 `test_emit_in_sync_context_falls_back_inline` 断言 `sink.records == []` | 已修复（2026-09：同步路径失败先入内存缓冲（上限 1000），下次有 loop 时经 drain 落 sink → export_failures） |
| R4 | `export_failures` 表有写无读：留痕机制"写"完整，但重试器/告警/运维回放脚本等消费端未实现，长期运行该表只增不减 | observability-adapters、persistence | `lubanagent/persistence/repositories/export_failure.py`（仅 CRUD，无消费方调用） | 已修复（2026-09：SafeBackend drain 收尾自动回放重试——成功删记录、失败计次（retry_count 列，迁移 0007）、默认 3 次后放弃保留行；后端适配器新增 replay；`list_given_up` 供运维查询） |
| R5 | 评测结果存在两套并行持久化：`EvalReport`（内存/JSON 文件）与 `eval_results` 表（DB 单一 score 浮点数）字段模型不同，`compare_reports` 完全不读 DB；`/v1/evals` 路由目前是 stub，未与任一侧真正打通 | observability-trace-cost、api-app-cli | `lubanagent/observability/evals/report.py`、`lubanagent/api/routes/evals.py` | 已修复（2026-09：评测领域模型下沉 `core/evaluation.py`；`luban eval` 完整报告落库（迁移 0008，逐 case 明细+聚合列）；`/v1/evals` 列表+详情接真实数据（旧 results stub 删除）；工作台评测页读真数据。compare_reports 仍走 JSON 文件——可移植基线载体，有意决策） |
| R6 | 鉴权配置缺失（`auth_enabled=True` 但未配 `api_key` 且无自定义 `AuthProvider`）会导致全部 `/v1/*` 请求 500，仅在启动期打一条 warning，容易被忽略造成"服务表面起来了但接口全挂" | api-app-cli | `lubanagent/api/auth.py`（`CONFIG_ERROR` 分支） | 已修复（2026-09：`create_app` 装配期（扩展扫描后）直接 raise CONFIG_ERROR 含三条修复出路；运行时 500 分支保留为纵深防御） |
| R7 | `luban init` 对已存在 key 的 Agent YAML 重复导入会持续追加新版本号，并非严格幂等，与部署文档"migrate/init 幂等，重起安全"的表述存在范围不一致 | api-app-cli | `lubanagent/cli/main.py`（`seed_defaults`/YAML 导入逻辑） | 已修复（2026-09：剔除 version/status 后内容深度相等即跳过导入，输出标 unchanged；内容有变化才追加版本） |
| R8 | Skill 沙箱（`sandbox.py`）的网络白名单仅为环境变量字符串标记，无 iptables/网络 namespace 等实际隔离；子进程完整继承宿主环境变量（含可能的密钥）；无 CPU/内存/磁盘 IO 限额。若未来开放非受信来源上传 Skill 包，存在 SSRF/数据外泄/资源耗尽风险面 | skill | `lubanagent/skill/sandbox.py:1-5`（docstring 自述局限）、`run_script` 的 `env = dict(os.environ)` | 部分修复（2026-09：env 白名单透传（宿主密钥默认不可见）+ RLIMIT_CPU/AS/NPROC/FSIZE 四项限额；**网络仍可达、文件系统未隔离**——开放非受信 Skill 包前须先补 OS 级网络隔离，docstring 如实声明） |
| R9 | 退款审批阈值（`amount > 500.0`）硬编码重复在 Tool 描述、Agent 指令 YAML、Graph 分支三处，无统一配置项来源，改动需人工同步三处 | app-user-land | `app/tools/customer_service/refund.py:20-27`、`app/agents/customer_service.yaml`、`app/agents/cs_refund.yaml`、`app/graphs/refund_flow.py:43` | 已修复（2026-09：单点常量 `app/config/refund_policy.py`，Tool/Graph import；YAML 指令为静态文本无法 import，守卫测试校验 YAML 数字与常量一致逼同步） |
| R10 | `KnowledgeStore.search` 是全量读表 + Python 内存 cosine 排序，非专用向量库，随 `knowledge_chunks` 数据量增长有明显性能天花板；`long_term_memories` 无过期/容量控制，长期运行可能无限增长 | knowledge-memory | `lubanagent/knowledge/store.py`、`lubanagent/memory/long_term.py` | 部分修复（2026-09：长期记忆加每 agent+user 容量上限（默认 1000，env 可调）超限淘汰最旧；top_k 钳制 [1,100] 修复 0/负数静默吞结果。全量扫描架构仍属首版有意裁剪，换专用向量库是明确后路） |
| R11 | HTTP 审批决定人的 `decided_by` 取自已校验的 `Principal.subject`（客户端同名字段仅兼容、不用于归因）；审批记录保存在进程内存，Trace 的审批解决事件携带决定人，可归因于 API Key 持有者，但共享 Key 无法证明独立自然人或职责分离，审批记录也无持久化；自动审批以 `auto-approve` 系统标记而非人工身份归因 | tool-mcp、api-app-cli | `lubanagent/api/routes/approvals.py`（取 `principal.subject`）、`lubanagent/api/auth.py`（内置 Key 主体为 `api-key-holder`）、`lubanagent/tool/approval.py`（进程内状态与自动审批标记）、`lubanagent/runtime/agent_loop.py`（`TOOL_APPROVAL_RESOLVED` 事件） | 部分修复（2026-10：审批记录持久化已修复——`approvals` 表（迁移 `0010`）+ `DbApprovalStore` + `create_app` 默认落库 + 跨进程 decide 轮询恢复（默认 2s）+ `try_resolve` 条件更新原子裁决；注意审批记录持久化 ≠ Run 自动恢复——普通 Run 已有逐步 checkpoint（2026-10 缺口 #2），但恢复须显式调 `POST /v1/runs/{run_id}/resume`（非终态且有 checkpoint），无自动重启恢复，审批闸门语义不随 resume 改变）。**自然人身份/职责分离仍开放**：共享 Key 只证明持有者，不能证明独立自然人；自动批准仍为 `auto-approve` 系统标记） |
| R12 | 独立 CLI 进程缺用户领地装配（教程写作时发现）：`luban eval run` 此前不扫描 EXTENSION_PACKAGES（带工具的 Agent 100%「工具已注销」）、无知识源连接器（带 knowledge 的 Agent 100% CONFIG_ERROR）、无审批 manager（审批型工具执行被拦截）——demo README 记载的旗舰命令从首发起从未真正跑通过 | api-app-cli、observability-trace-cost | `lubanagent/cli/main.py`（修复前 `_eval_run_async` 直接构造 AgentRuntime） | 已修复（2026-09：eval 启动三件套——①`discover_packages` 扩展扫描（与 create_app 共用助手，抽进 `extension/decorators.py`）；②知识源自举 `restore_knowledge_sources`（runtime 层新函数，按库中已有 chunk 重建通用 SQL connector，种子/托管源行为一致；**自定义 retrieve 逻辑的连接器不适用**，仍需用户领地装配）；③`--auto-approve`（AutoApproveManager 请求即批，缺省仅断言调用意图不执行）。旗舰验证：cs_basic 真模型 5/5（judge 用例存在 4/5↔5/5 评分方差，LLM-judge 固有）；cs_knowledge 2/2） |
| R13 | ContextBuilder 变量渲染用 `str.format_map`，指令含 JSON 花括号字面量（supervisor 路由指令的输出格式样例是刚需）时 `Invalid format specifier` ValueError 直接打死 Run——**内置 cs-triage 真实指令即含此样例，多 Agent 团队 Run 首发即全挂**；既有测试用短指令造 Agent，从未跑过真实 YAML（盲区） | runtime、multiagent | `lubanagent/runtime/context_builder.py`（修复前 `text.format_map(_SafeDict(merged))`）；复现：`POST /v1/teams/cs-team/runs` → INTERNAL_ERROR 子 Run 未成功 | 已修复（2026-09：占位符改精确正则替换——整个花括号内恰为一个标识符且与已声明变量同名才渲染，JSON 字面量/未声明占位符原样保留；不再支持 format_map 格式规格语法 `{name:>10}`（prompt 变量场景无使用）。3 个回归测试；端到端验证 cs-team 与教程团队均 completed 且路由正确） |
| R14 | 普通 Run resume 的互斥保护仅限单进程：活跃判定（`CancellationRegistry.is_active`）与 per-run `asyncio.Lock` 都是进程内存态，多 worker/多实例部署下同一 Run 可被两个进程同时 resume（各自从同一 checkpoint 续跑，budget/trace 双写） | runtime、api-app-cli | `lubanagent/runtime/agent_loop.py`（`stream_resume` 的 `_resume_lock` 与活跃校验均为进程内），单进程部署（uvicorn 单 worker，`scripts/dev.sh`/`docker-compose.yml` 现状）不受影响 | 开放（2026-10 登记；跨进程互斥需共享存储锁或 Run 行级状态机，未纳入本次契约；文档已如实声明「本进程活跃」边界） |

## 复盘记录

<!-- 本节登记真实发生过的故障复盘。首次初始化时为空，不可编造；有真实事件材料时按日期倒序补充。 -->

无。

## 测试稳定性观察

<!-- 高负载下偶发、单跑无法复现的测试抖动登记（非故障复盘，供后续排查）。 -->

- **2026-10**：全量回归（897 用例并发负载）下 `tests/lubanagent/runtime/test_agent_loop.py::test_sse_event_sequence` 与 `::test_hooks_wired` 偶发 hook 顺序断言失败（如 `post_step` 序列断言出 `['tool', 'model', ...]` 乱序），单文件/单用例复跑均稳定通过；复跑全量亦可通过（同一提交多次全量运行约 2/5 次出现、每次至多 1 例）。疑似 `astream_events` v2 在高负载下模型事件与工具事件到达顺序抖动，非 run-checkpoint-resume 单元引入（两用例均不注入 checkpointer）。建议后续：hook 调用序列断言改为集合/计数语义，或在 `_bridge_tool`/模型步的 `post_step` 间补充顺序锚点后复测。
