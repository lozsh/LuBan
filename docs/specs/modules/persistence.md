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

# persistence 模块

## 定位

persistence 模块管理 LubanAgent 全部持久化状态的落库与访问，核心表覆盖 Agent 定义版本化（`agents`/`agent_versions`）、Prompt 库（`prompts`/`prompt_versions`，与 agents 同型的指针 + 不可变快照）、运行执行记录（`runs`）、可观测双写（`traces`/`trace_events`/`token_usages`/`cost_records`/`export_failures`）、模型中心（`models`/`brain_mappings`）、能力/评测资产（`skills`/`mcp_servers`/`eval_datasets`/`eval_results`）、LangGraph 检查点（`checkpoints`）、审批记录（`approvals`）与知识/记忆（`knowledge_chunks`/`knowledge_sources`/`session_messages`/`session_summaries`/`long_term_memories`），共 23 张表；聚合根实体以 `AgentRow`/`PromptRow`/`RunRow`/`TraceRow` 为主，SQLAlchemy 声明基类为 `Base`（`lubanagent/persistence/base.py:6`）。数据库开发期 SQLite、生产 MySQL 8.0+，通过 `Database`/`create_database` 统一异步会话入口（`lubanagent/persistence/session.py:11`、`:36`）。

## 接口契约

### HTTP

本模块无 HTTP 接口，对外契约是仓储层（Repository）与直接 ORM 访问两种 Python API 形态。

### RPC / MQ / Cron

无。

### 仓储层 Python API

以下 11 个 Repository 类经 `lubanagent/persistence/__init__.py:5` 统一导出，均以 `AsyncSession` 构造、方法均为 `async`，返回 `lubanagent.core` 领域对象（含 `EvalRepository` 的 `EvalDataset`/`EvalReport`/`EvalReportSummary`/`EvalReportDetail`、`McpServerRepository` 的 `McpServer` 与 `ApprovalRepository` 的 `ApprovalRequest`，均已领域对象化）。另有两个 Store 实现随包导出（非 Repository 构造型）：`DatabaseMcpServerStore` 与 `DbApprovalStore`（`ApprovalStore` 协议实现，每操作独立短 session，见下表末行）。

| 类 | 方法 | 参数 | 返回 | 说明 |
|---|---|---|---|---|
| `AgentRepository`（`repositories/agent.py:11`） | `create_agent` | `definition: AgentDefinition` | `AgentDefinition` | 落 `agents` 行 + `agent_versions` 首版本行 |
| | `get_agent` | `key: str` | `AgentDefinition \| None` | 取当前版本合成领域对象 |
| | `create_version` | `agent_key: str, definition: AgentDefinition` | `None` | 追加不可变版本快照并推进 `current_version`；`agent` 不存在抛 `AGENT_NOT_FOUND`，`(agent_id, version)` 重复抛 `VALIDATION_ERROR`（`repositories/agent.py:54`、`:68`） |
| | `get_version` | `agent_key: str, version: int` | `AgentVersion \| None` | 取指定版本快照 |
| | `list_agents` | 无 | `list[AgentDefinition]` | 按 `id` 升序全量列出 |
| | `update_status` | `agent_key: str, status: AgentStatus` | `None` | 更新 `agents.status`；不存在抛 `AGENT_NOT_FOUND` |
| `PromptRepository`（`repositories/prompt.py:17`） | `create_prompt` | `definition: PromptDefinition` | `PromptDefinition` | 落 `prompts` 行 + `version=definition.version` 的 `prompt_versions` 行 |
| | `get_prompt` | `key: str` | `PromptDefinition \| None` | 取 prompts 行 + 当前版本合成领域对象 |
| | `create_version` | `prompt_key: str, definition: PromptDefinition` | `None` | 追加不可变版本快照并推进 `current_version`；`prompt` 不存在抛 `PROMPT_NOT_FOUND`，`(prompt_id, version)` 重复抛 `VALIDATION_ERROR` |
| | `get_version` | `prompt_key: str, version: int` | `PromptVersion \| None` | 取指定版本快照 |
| | `list_versions` | `prompt_key: str` | `list[PromptVersion]` | 按 `version` 降序；prompt 不存在返回空列表 |
| | `list_prompts` | 无 | `list[PromptDefinition]` | 按 `id` 升序全量列出 |
| `ModelRepository`（`repositories/model.py:11`） | `upsert_model` | `definition: ModelDefinition` | `None` | 按 `key` 存在则更新、不存在则插入；**写入前统一校验**：插入与更新两条路径均先调 `validate_model_connection`（连接边界/`params` 白名单，`repositories/model.py:15-45`），非法定义拒绝写入；更新路径请求 `base_url` 为 `None` 表示沿用旧行值（列表接口不返回旧连接地址），沿用后以合并值再校验 |
| | `get_model` | `key: str` | `ModelDefinition \| None` | 读取经 `_to_domain`（`ModelDefinition.model_construct` 跳过校验器，`repositories/model.py:102-114`），历史脏记录可读取供管理界面安全展示；执行须再经 `create_openai_compatible` 构造入口校验并拒绝 |
| | `list_models` | `enabled_only: bool = True` | `list[ModelDefinition]` | 读取同 `get_model`（`_to_domain` 跳过校验器语义） |
| | `set_brain_mapping` | `env: str, profile: BrainProfile, model_key: str` | `None` | 按 `(env, profile)` upsert |
| | `has_brain_mapping` | `env: str, profile: BrainProfile` | `bool` | 该槽位是否已有映射行（不校验指向模型存在，供 `seed_defaults` 只填空位） |
| | `get_brain_mapping` | `env: str, profile: BrainProfile` | `ModelDefinition \| None` | join `models` 取完整定义；读取同 `get_model`（`_to_domain` 跳过校验器语义） |
| `RunRepository`（`repositories/run.py:24`） | `create_run` | `run: Run` | `None` | 校验 `agent`/`agent_version` 存在，否则抛 `AGENT_NOT_FOUND`/`AGENT_VERSION_NOT_FOUND` |
| | `get_run` | `run_id: str` | `Run \| None` | — |
| | `update_run` | `run: Run` | `None` | 覆盖状态/输出/结束原因/用量/费用/error/时间戳；不存在抛 `RUN_NOT_FOUND` |
| | `list_runs` | `agent_key: str \| None, *, session_id: str \| None, user_id: str \| None, limit: int = 50` | `list[Run]` | 按 `created_at desc, id desc`；`user_id` 为透传观测标签过滤（引擎不做用户管理） |
| | `add_token_usage` | `run_id, step_index, model_key, input_tokens, output_tokens` | `None` | 写 `token_usages` 明细行 |
| | `add_cost_record` | `run_id, model_key, cost, currency="CNY"` | `None` | 写 `cost_records` 明细行 |
| | `run_usage` | `run_id: str` | `tuple[int, int, float]` | 该 Run 账本已消费量 `(input_tokens 合计, output_tokens 合计, cost 合计)`（`repositories/run.py:114`）；token 列 NULL（模型未回报）按 0 计入合计。供 resume 以账本实耗量重建 `BudgetGuard`，崩溃续跑不重复放水 |
| `TraceRepository`（`repositories/trace.py:12`） | `create_trace` | `trace: Trace` | `None` | 写 `traces` 头行 |
| | `get_trace_by_run` | `run_id: str` | `Trace \| None` | — |
| | `add_event` | `event: TraceEvent` | `None` | `trace` 不存在抛 `RUN_NOT_FOUND` |
| | `list_events` | `run_id: str` | `list[TraceEvent]` | 按 `created_at, id` 升序 |
| | `finish_trace` | `run_id: str, status: RunStatus, ended_at: datetime` | `None` | `trace` 不存在抛 `RUN_NOT_FOUND` |
| `CostRepository`（`repositories/cost.py:13`） | `aggregate_by_agent` | `since: datetime \| None` | `list[dict]` | join `runs`+`token_usages`+`agents`，按 `agent_key` 聚合 |
| | `aggregate_by_session` | `agent_key: str \| None` | `list[dict]` | 按 `session_id` 聚合（仅统计有会话 Run） |
| | `aggregate_by_day` | `days: int = 7` | `list[dict]` | 按 `date(created_at)` 分组 |
| | `cost_rows` | `agent_key: str \| None` | `list[dict]` | join `cost_records`+`runs`，按 `model_key` 聚合 |
| `SkillRepository`（`repositories/skill.py:10`） | `upsert` | `skill: SkillPackage, enabled: bool = True` | `None` | 同 `key` 仅当 `content_hash` 变化才更新 `version`/`manifest` |
| | `get` | `key: str` | `SkillPackage \| None` | — |
| | `list` | 无 | `list[SkillPackage]` | — |
| `McpServerRepository`（`repositories/mcp.py:10`） | `upsert` | `server: McpServer` | `None` | 按 `key` upsert（领域对象承载，DB 列零改动，`core/mcp.py`） |
| | `get` | `key: str` | `McpServer \| None` | — |
| | `list` | 无 | `list[McpServer]` | 按 `id` 升序 |
| `EvalRepository`（`repositories/eval.py:38`） | `create_dataset` | `dataset: EvalDataset, *, version: str = "1"` | `None` | key 唯一；调用方先查重，重复插入由唯一约束兜底 |
| | `get_dataset` | `key: str` | `EvalDataset \| None` | — |
| | `save_report` | `report: EvalReport` | `int`（结果行 id） | `dataset_key` 对应数据集不存在抛 `VALIDATION_ERROR`；写完整报告 JSON + 聚合列（`pass_rate`/`passed_cases`/`total_cases`/`wrong_answer_rate` 由报告计算） |
| | `get_detail` | `result_id: int` | `EvalReportDetail \| None` | join `eval_datasets` 取 `dataset_key` + 解析 `report` 全量逐 case 结果 |
| | `list_summaries` | `*, dataset_key: str \| None, agent_key: str \| None, limit: int = 50` | `list[EvalReportSummary]` | join `eval_datasets`，按 `id` 降序；聚合列直出不解析报告 JSON |
| `ExportFailureRepository`（`repositories/export_failure.py:15`） | `record` | `backend, run_id, event_type, payload, error` | `None` | 写失败留痕（`retry_count` 默认 0） |
| | `list_pending` | `backend: str \| None, limit: int = 100, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS(3)` | `list[dict]` | 仍可重试（`retry_count < max_attempts`）的记录，按 `id` 升序 |
| | `list_given_up` | `backend: str \| None, limit: int = 100, *, max_attempts: int = 3` | `list[dict]` | 已放弃（尝试次数达上限）的记录，按 `id` 降序，供运维排查（行保留不再自动重试） |
| | `mark_retry_failed` | `ids: list[int]` | `None` | 一次重试失败：批量 `retry_count += 1`、`retried_at = now()` |
| | `delete` | `ids: list[int]` | `None` | 回放成功后删除留痕（消费闭环终点，`SafeBackend._sweep_failures` 调用） |
| `ApprovalRepository`（`repositories/approval.py:29`） | `create` | `request: ApprovalRequest` | `None` | 落 `approvals` 行（`flush` 不 commit，事务边界归 `Database.session()`） |
| | `get` | `approval_id: str` | `ApprovalRequest \| None` | — |
| | `list_pending` | `*, run_id: str \| None = None` | `list[ApprovalRequest]` | 仅 `status='pending'`，按 `created_at, id` 升序 |
| | `list_all` | `*, run_id: str \| None = None` | `list[ApprovalRequest]` | 全部记录含终态（审计回查） |
| | `try_resolve` | `approval_id, status: Literal["approved","rejected","expired"], *, decided_by: str \| None` | `bool` | 条件更新 `UPDATE ... WHERE status='pending'`（`repositories/approval.py:69-83`）：受影响行数=1 才成功，并发/跨进程双 decide 恰一个成功 |
| `DbApprovalStore`（`repositories/approval.py:86`，非 Repository 构造型） | 实现 `lubanagent.tool` 的 `ApprovalStore` 协议（`save/get/list_pending/list_all/try_resolve`） | `db: Database` | 同协议 | 每操作独立短 session（同 `TraceWriter` 风格），供 `app/factory.py` 注入 `ApprovalManager(store=DbApprovalStore(db))`；经 `lubanagent.persistence` 导出 |

### 模块级函数：`resolve_prompt_reference`

`resolve_prompt_reference(session, definition) -> AgentDefinition`（`repositories/prompt.py:141`，经 `lubanagent/persistence/__init__.py` 导出）是 Agent 定义对 Prompt 库引用的统一解析入口，由 `api/routes/agents.py` 的创建 Agent / 追加版本两个端点在**落库前**调用：

- `definition.prompt_key` 为 `None` → 原样返回（存量纯内嵌路径，零行为变化）；
- 非空 → 经 `PromptRepository` 取 prompt 当前版（`prompt_version` 为 `None`）或钉住版（非 `None` 且 ≠ 当前版时二次查 `prompt_versions`，查不到抛 `PROMPT_VERSION_NOT_FOUND`；prompt 不存在抛 `PROMPT_NOT_FOUND`），把 content 写入 `instructions`、变量合并进 `prompt_variables`（Agent 自带同名变量覆盖 Prompt 变量）、`prompt_version` 回填为实际解析到的版本号，返回 `model_copy(update=...)` 的新定义。

快照落地语义：解析结果随 `AgentRepository.create_agent`/`create_version` 落进 `agent_versions.definition` JSON，运行期只读快照、不回查 `prompts` 表（回放确定性）；整份定义回传（含旧 `prompt_version`）会钉住旧版，Prompt 新版不自动作用于引用方——Agent 吃新版必须显式产生新 Agent 版本。

### 无独立 Repository 的直接 ORM 访问

以下三类不经 Repository 类，由所在模块直接 `select`/`insert`/`delete` ORM 模型（各自持有 `Database` 实例）：

- **`LubanCheckpointer`**（`lubanagent/persistence/checkpoint.py:35`，经 `lubanagent/persistence/__init__.py` 导出）：`aget_tuple`/`aput`/`alist`/`adelete_thread` 直接操作 `CheckpointRow`；`aput_writes`/`put_writes` 为 no-op（无 writes 表，pending writes 不落库）。`lubanagent/graph/checkpoint.py` 保留为 re-export 垫片（旧导入路径 `lubanagent.graph.LubanCheckpointer` 零破坏）；实现下沉 persistence 的原因：普通 Agent Run 与手写图/多 Agent 编排共用同一 Saver，而分层契约禁止 `runtime → graph` 依赖，persistence 是两者共同的下层。
- **`KnowledgeStore`**（`lubanagent/knowledge/store.py:33`）：`add_chunks`/`delete_source`/`list_chunks`/`count_chunks`/`search` 直接操作 `KnowledgeChunkRow`；`search` 全量读后 Python 端算 cosine 排序。
- **`KnowledgeSourceManager`**（`lubanagent/knowledge/manager.py:66`）：直接操作 `KnowledgeSourceRow`（`knowledge_sources` 表，工作台托管知识源元数据，迁移 `0006`）——`create_source`/`delete_source`/`rechunk`/`restore` 读写行，`list_sources`/`get_source` 查询；文件落盘与切块灌库委托 `ingest_files`/`KnowledgeStore`。
- **`SessionMemory`/`SummaryMemory`/`LongTermMemory`**（`lubanagent/memory/session.py:34`、`summary.py:59`、`long_term.py:11`）：直接操作 `SessionMessageRow`/`SessionSummaryRow`/`LongTermMemoryRow`。

## 数据模型

说明：`created_at`/`updated_at` 均由 `server_default=func.now()`（`updated_at` 另加 `onupdate=func.now()`）生成，非纯审计留痕字段——`runs.created_at`/`traces.created_at`/`trace_events.created_at` 等被 `list_runs`/`list_events`/`aggregate_by_day` 等方法用作排序或分组依据（见「接口契约」表），故在下表逐一列出，不套用文档跳过审计字段（`creator`/`creator_id`/`creator_name`/`modifier`/`create_time`/`update_time`）的规则。

### agents

对应实体：`AgentRow`（`models/agent.py:14`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| key (String(128)) | key (str) | ✓ | — | 唯一键，Agent 业务标识 |
| name (String(256)) | name (str) | ✓ | — | 展示名 |
| description (Text) | description (str) | ✓ | `""` | 描述 |
| current_version (Integer) | current_version (int) | ✓ | — | 指向 `agent_versions.version` 当前生效版本 |
| status (String(32)) | status (str) | ✓ | `AgentStatus.DRAFT`（`draft`） | 枚举值：`draft`/`published`/`deprecated`（`lubanagent/core/definition.py:13`） |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| updated_at (DateTime(tz)) | updated_at (datetime) | ✓ | `func.now()`（server_default + onupdate） | 更新时间，行变更时自动刷新 |

索引：
- 主键 `id`；`key` 唯一约束（无独立命名索引）。

### agent_versions

对应实体：`AgentVersionRow`（`models/agent.py:31`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| agent_id (Integer, FK→agents.id) | agent_id (int) | ✓ | — | → `agents`.`AgentRow` |
| version (Integer) | version (int) | ✓ | — | 版本号，与 `agent_id` 联合唯一 |
| definition (JSON) | definition (dict[str, Any]) | ✓ | — | `AgentDefinition` 全量序列化快照，不可变 |
| status (String(32)) | status (str) | ✓ | — | 该版本发布时的 `AgentStatus` 快照 |
| published_at (DateTime(tz)) | published_at (datetime \| None) | — | NULL | 发布时间 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间，版本快照不可变故无 `updated_at` |

索引：
- 唯一约束 `UniqueConstraint("agent_id", "version")`（`models/agent.py:33`）— 保证同一 Agent 同版本号不重复，供 `create_version` 的乐观并发校验（`IntegrityError` → `VALIDATION_ERROR`）。

### prompts

对应实体：`PromptRow`（`models/prompt.py:13`）。Prompt 库主记录（指针表），与 `agents` 同型。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| key (String(128)) | key (str) | ✓ | — | 唯一键，Prompt 业务标识 |
| name (String(256)) | name (str) | ✓ | — | 展示名 |
| description (Text) | description (str) | ✓ | `""` | 描述 |
| current_version (Integer) | current_version (int) | ✓ | `1` | 指向 `prompt_versions.version` 当前生效版本 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| updated_at (DateTime(tz)) | updated_at (datetime) | ✓ | `func.now()`（server_default + onupdate） | 更新时间，`current_version` 推进时自动刷新 |

索引：
- 主键 `id`；`key` 唯一约束。

### prompt_versions

对应实体：`PromptVersionRow`（`models/prompt.py:29`）。发布即冻结的不可变版本快照，与 `agent_versions` 同型。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| prompt_id (Integer, FK→prompts.id) | prompt_id (int) | ✓ | — | → `prompts`.`PromptRow` |
| version (Integer) | version (int) | ✓ | — | 版本号，与 `prompt_id` 联合唯一 |
| content (Text) | content (str) | ✓ | `""` | Prompt 全文，即 Agent `instructions` 的来源文本 |
| variables (JSON) | variables (dict[str, Any]) | ✓ | `dict` | Prompt 变量表 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间，版本快照不可变故无 `updated_at` |

索引：
- 唯一约束 `UniqueConstraint("prompt_id", "version")`（`models/prompt.py:31`）— 保证同一 Prompt 同版本号不重复，供 `PromptRepository.create_version` 的并发校验（`IntegrityError` → `VALIDATION_ERROR`）。表结构由迁移 `0005_prompt_library.py`（revision `a7b8c9d0e1f2`，down_revision `f1a2b3c4d5e6`）创建。

### checkpoints

对应实体：`CheckpointRow`（`models/checkpoint.py:13`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| thread_id (String(128)) | thread_id (str) | ✓ | — | 复合主键之一，LangGraph 会话线程 id |
| checkpoint_ns (String(128)) | checkpoint_ns (str) | ✓ | `""` | 复合主键之一，checkpoint 命名空间 |
| checkpoint_id (String(128)) | checkpoint_id (str) | ✓ | — | 复合主键之一，uuid6（时间有序） |
| parent_checkpoint_id (String(128)) | parent_checkpoint_id (str \| None) | — | NULL | 父检查点 id，构成检查点链 |
| type (String(64)) | type (str) | ✓ | — | serde 类型标记（`JsonPlusSerializer` 输出） |
| checkpoint (LargeBinary) | checkpoint (bytes) | ✓ | — | 序列化后的检查点二进制内容 |
| metadata (JSON) | metadata_ (dict[str, Any]) | ✓ | `dict` | 列名 `metadata`，属性名 `metadata_`（`Declarative` 保留名冲突，`models/checkpoint.py:23`） |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间；检查点链的时间顺序实际由 `checkpoint_id`（uuid6）承担，见「名词」节 |

索引：
- 复合主键 `(thread_id, checkpoint_ns, checkpoint_id)`；无 writes 表（`aput_writes` no-op），无额外二级索引。

### mcp_servers

对应实体：`McpServerRow`（`models/misc.py:27`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| key (String(128)) | key (str) | ✓ | — | 唯一键 |
| name (String(256)) | name (str) | ✓ | — | 展示名 |
| transport (String(16)) | transport (str) | ✓ | — | 传输方式，取值范围 `Literal["stdio", "http"]`（`lubanagent/mcp/client.py:25` `McpServerConfig.transport`、`lubanagent/mcp/server.py:44` `run_server(transport=...)` 均以此类型约束；DB 列本身无 CHECK 约束，取值范围由调用方类型系统保证） |
| config (JSON) | config (dict[str, Any]) | ✓ | `dict` | 连接配置 |
| enabled (Boolean) | enabled (bool) | ✓ | `True` | 启用开关 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

索引：
- 主键 `id`；`key` 唯一约束。

### eval_datasets

对应实体：`EvalDatasetRow`（`models/misc.py:41`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| key (String(128)) | key (str) | ✓ | — | 唯一键 |
| name (String(256)) | name (str) | ✓ | — | 展示名 |
| version (String(32)) | version (str) | ✓ | — | 数据集版本号 |
| cases (JSON) | cases (list[dict[str, Any]]) | ✓ | `list` | 评测用例集合 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

索引：
- 主键 `id`；`key` 唯一约束。

### models

对应实体：`ModelRow`（`models/model_center.py:13`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| key (String(128)) | key (str) | ✓ | — | 唯一键，模型注册标识；被 `brain_mappings.model_key` 外键引用 |
| provider (String(64)) | provider (str) | ✓ | — | 厂商标识 |
| model_name (String(256)) | model_name (str) | ✓ | — | 厂商侧模型名 |
| factory (String(64)) | factory (str) | ✓ | — | 对应工厂实现标识（如 openai_compat） |
| capability (JSON) | capability (dict[str, Any]) | ✓ | `dict` | 能力描述（如是否支持 vision/tools） |
| params (JSON) | params (dict[str, Any]) | ✓ | `dict` | 调用参数（temperature 等） |
| base_url (String(512)) | base_url (str \| None) | — | NULL | 自定义 endpoint |
| api_key_env (String(128)) | api_key_env (str \| None) | — | NULL | API Key 所在环境变量名（不落密钥本身） |
| enabled (Boolean) | enabled (bool) | ✓ | `True` | 启用开关，`list_models(enabled_only=True)` 默认过滤 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| updated_at (DateTime(tz)) | updated_at (datetime) | ✓ | `func.now()`（server_default + onupdate） | 更新时间，行变更时自动刷新 |

索引：
- 主键 `id`；`key` 唯一约束。

### skills

对应实体：`SkillRow`（`models/misc.py:13`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| key (String(128)) | key (str) | ✓ | — | 唯一键，即 `SkillPackage.meta.name`（`repositories/skill.py:11`） |
| name (String(256)) | name (str) | ✓ | — | 展示名 |
| version (String(32)) | version (str) | ✓ | — | `SkillPackage.meta.version`，仅内容 hash 变化时更新 |
| manifest (JSON) | manifest (dict[str, Any]) | ✓ | `dict` | `SkillPackage` 全量序列化（含 `content_hash`） |
| enabled (Boolean) | enabled (bool) | ✓ | `True` | 启用开关，每次 `upsert` 均跟随入参覆盖 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

索引：
- 主键 `id`；`key` 唯一约束。

### brain_mappings

对应实体：`BrainMappingRow`（`models/model_center.py:34`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| env (String(32)) | env (str) | ✓ | — | 环境标识（如 dev/prod），与 `profile` 联合唯一 |
| profile (String(16)) | profile (str) | ✓ | — | `BrainProfile` 枚举值：`fast`/`smart`/`cheap`/`vision`（`lubanagent/core/brain.py:6`） |
| model_key (String(128), FK→models.key) | model_key (str) | ✓ | — | → `models`.`ModelRow`，四槽位到具体模型的映射 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| updated_at (DateTime(tz)) | updated_at (datetime) | ✓ | `func.now()`（server_default + onupdate） | 更新时间，`set_brain_mapping` upsert 时自动刷新 |

索引：
- 唯一约束 `UniqueConstraint("env", "profile")`（`models/model_center.py:36`）— 支撑 `set_brain_mapping`/`get_brain_mapping` 的按 `(env, profile)` 精确定位。

### eval_results

对应实体：`EvalResultRow`（`models/misc.py:54`）——`EvalReport` 全量序列化（SkillRepository 同款）+ 列表页聚合列，领域对象经 `EvalRepository` 承载（`core/evaluation.py`）。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| dataset_id (Integer, FK→eval_datasets.id) | dataset_id (int) | ✓ | — | → `eval_datasets`.`EvalDatasetRow` |
| agent_key (String(128)) | agent_key (str) | ✓ | — | 被评测 Agent 标识（非外键，冗余存储便于查询） |
| agent_version (Integer) | agent_version (int) | ✓ | — | 被评测版本号 |
| model_key (String(128)) | model_key (str) | ✓ | — | 本次评测实际命中的模型键（CLI 落库前回填） |
| pass_rate (Float) | pass_rate (float) | ✓ | — | 通过率（聚合列，由仓储从报告计算写入） |
| passed_cases (Integer) | passed_cases (int) | ✓ | `0` | 通过用例数（聚合列） |
| total_cases (Integer) | total_cases (int) | ✓ | `0` | 总用例数（聚合列） |
| wrong_answer_rate (Float) | wrong_answer_rate (float) | ✓ | `0.0` | 错答率（聚合列，防错答硬指标目标 0） |
| report (JSON) | report (dict[str, Any]) | ✓ | `dict` | 完整 `EvalReport` 序列化（逐 case pass/fail、断言明细、judge 结论、run_id/output/error） |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

列改造见迁移 `0008_eval_results_report_details`（旧 `score`→`pass_rate`、`details`→`report`，追加三个聚合列；旧行数据保留、聚合列 server_default 0 兜底）。

索引：
- `ix_eval_results_dataset_id (dataset_id)`（`mapped_column(..., index=True)`，`models/misc.py:60`）— 支撑 `list_summaries`/`get_detail` 按 `dataset_key` join 查询。

### runs

对应实体：`RunRow`（`models/run.py:14`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (String(36)) | id (str) | ✓ | — | 主键，UUID 字符串 |
| agent_id (Integer, FK→agents.id) | agent_id (int) | ✓ | — | → `agents`.`AgentRow` |
| agent_version_id (Integer, FK→agent_versions.id) | agent_version_id (int) | ✓ | — | → `agent_versions`.`AgentVersionRow` |
| session_id (String(128)) | session_id (str \| None) | — | NULL | 会话 id，跨轮串联 Run |
| user_id (String(128)) | user_id (str \| None) | — | NULL | 透传观测标签（如业务侧用户 id），无用户管理语义；支撑"哪个用户的 Run"排查（`models/run.py:21`，迁移 `0004` 加列） |
| parent_run_id (String(36)) | parent_run_id (str \| None) | — | NULL | 父 Run id，支撑编排 Run 的子 Run 归属（预算继承/取消传播，架构 §10.6） |
| status (String(32)) | status (str) | ✓ | `RunStatus.PENDING`（`pending`） | 状态机枚举，见「行为」节 |
| input (JSON) | input (dict[str, Any]) | ✓ | `dict` | 运行输入 |
| output (Text) | output (str \| None) | — | NULL | 运行输出（终态填充） |
| brain_profile (String(16)) | brain_profile (str) | ✓ | — | 本次运行使用的大脑槽位 |
| model_key (String(128)) | model_key (str \| None) | — | NULL | 实际解析出的模型 key |
| end_reason (String(32)) | end_reason (str \| None) | — | NULL | `EndReason` 枚举：`final_answer`/`step_limit`/`budget_exceeded`/`timeout`/`cancelled`/`error`/`human_handoff`（`lubanagent/core/run.py:22`） |
| token_usage (JSON) | token_usage (dict[str, Any]) | ✓ | `dict` | `TokenUsage` 汇总快照（区别于明细表 `token_usages`） |
| cost (Float) | cost (float) | ✓ | `0.0` | 费用汇总（元），区别于明细表 `cost_records` |
| error (JSON) | error (dict[str, Any] \| None) | — | NULL | 失败详情 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间；`list_runs` 按 `created_at desc, id desc` 排序、`CostRepository.aggregate_by_day` 按 `date(created_at)` 分组的依据 |
| started_at (DateTime(tz)) | started_at (datetime \| None) | — | NULL | 开始时间 |
| ended_at (DateTime(tz)) | ended_at (datetime \| None) | — | NULL | 结束时间 |

索引：
- `ix_runs_agent_id (agent_id)` — 支撑 `list_runs(agent_key=...)` 按 Agent 过滤。
- `ix_runs_session_id (session_id)` — 支撑 `list_runs(session_id=...)` 及 `CostRepository.aggregate_by_session` 按会话聚合。
- `ix_runs_user_id (user_id)` — 支撑 `list_runs(user_id=...)` 按观测用户标签过滤（迁移 `0004_runs_user_id.py` 新增）。

### cost_records

对应实体：`CostRecordRow`（`models/trace.py:55`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| run_id (String(36), FK→runs.id) | run_id (str) | ✓ | — | → `runs`.`RunRow`，费用明细（双写模型明细表，架构 §6.1） |
| model_key (String(128)) | model_key (str) | ✓ | — | 计费所属模型 |
| cost (Float) | cost (float) | ✓ | `0.0` | 单笔费用 |
| currency (String(8)) | currency (str) | ✓ | `"CNY"` | 币种 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

索引：
- `ix_cost_records_run_id (run_id)` — 支撑 `CostRepository.cost_rows`/`aggregate_by_agent` 等按 `run_id` join `runs` 的聚合查询。

### token_usages

对应实体：`TokenUsageRow`（`models/trace.py:41`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| run_id (String(36), FK→runs.id) | run_id (str) | ✓ | — | → `runs`.`RunRow`，token 用量明细（双写模型明细表） |
| step_index (Integer) | step_index (int \| None) | — | NULL | 所属执行步骤序号 |
| model_key (String(128)) | model_key (str) | ✓ | — | 调用的模型标识 |
| input_tokens (Integer) | input_tokens (int) | ✓ | `0` | 输入 token 数 |
| output_tokens (Integer) | output_tokens (int) | ✓ | `0` | 输出 token 数 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

索引：
- `ix_token_usages_run_id (run_id)` — 支撑 `CostRepository` 各聚合方法按 `run_id` join `runs`/`agents`。

### traces

对应实体：`TraceRow`（`models/trace.py:14`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (String(36)) | id (str) | ✓ | — | 主键，格式 `trace-{run.id}`（`TraceWriter.start_run`，`observability/trace/writer.py:35`） |
| run_id (String(36), FK→runs.id) | run_id (str) | ✓ | — | → `runs`.`RunRow`，唯一约束（一 Run 一 Trace） |
| status (String(32)) | status (str) | ✓ | — | 镜像 `RunStatus`，随 `finish_trace` 更新为终态 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| started_at (DateTime(tz)) | started_at (datetime \| None) | — | NULL | — |
| ended_at (DateTime(tz)) | ended_at (datetime \| None) | — | NULL | — |

索引：
- `run_id` 唯一约束（`mapped_column(..., unique=True)`，`models/trace.py:18`）— 保证一 Run 一 Trace，同时作为 `get_trace_by_run` 的查询路径。

### trace_events

对应实体：`TraceEventRow`（`models/trace.py:27`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (String(36)) | id (str) | ✓ | — | 主键，格式 `evt-{uuid4().hex}` |
| trace_id (String(36), FK→traces.id) | trace_id (str) | ✓ | — | → `traces`.`TraceRow` |
| run_id (String(36)) | run_id (str) | ✓ | — | 冗余存储 `run_id`（非外键），便于跳过 `traces` 直查事件 |
| step_index (Integer) | step_index (int \| None) | — | NULL | 所属执行步骤序号 |
| type (String(64)) | type (str) | ✓ | — | `TraceEventType` 枚举，见「行为」节 |
| payload (JSON) | payload (dict[str, Any]) | ✓ | `dict` | 事件负载 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间；`list_events` 按 `created_at, id` 升序排序的依据 |

索引：
- `ix_trace_events_run_id (run_id)` — 支撑 `TraceRepository.list_events(run_id)` 直查，无需先 join `traces`。

### export_failures

对应实体：`ExportFailureRow`（`models/trace.py:68`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| backend (String(64)) | backend (str) | ✓ | — | 外发观测后端标识（如 langfuse/langsmith） |
| run_id (String(36)) | run_id (str) | ✓ | — | 关联 Run（非外键） |
| event_type (String(64)) | event_type (str) | ✓ | — | 外发失败的事件类型 |
| payload (JSON) | payload (dict[str, Any]) | ✓ | `dict` | 失败时的原始负载，供重试回放 |
| error (Text) | error (str) | ✓ | — | 错误信息 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| retried_at (DateTime(tz)) | retried_at (datetime \| None) | — | NULL | 最后一次重试尝试时间 |
| retry_count (Integer) | retry_count (int) | ✓ | `0`（server_default "0"，迁移 `0007_export_failures_retry_count.py` 新增） | 重试尝试失败次数；达 `DEFAULT_MAX_ATTEMPTS=3` 即放弃——行保留供 `list_given_up` 运维查询，不再自动重试 |

索引：
- `ix_export_failures_backend (backend)` — 支撑 `ExportFailureRepository.list_pending(backend=...)` 按后端过滤待重试记录。

### knowledge_chunks

对应实体：`KnowledgeChunkRow`（`models/knowledge.py:13`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| source (String(128)) | source (str) | ✓ | — | 知识来源标识 |
| chunk_index (Integer) | chunk_index (int) | ✓ | — | 同一 `source` 内的切块序号 |
| content (Text) | content (str) | ✓ | — | 切块文本 |
| embedding (JSON) | embedding (list[float]) | ✓ | — | 向量，JSON float 数组存储（非专用向量库，`KnowledgeStore` 用 Python cosine 检索） |
| metadata (JSON) | chunk_metadata (dict[str, Any]) | ✓ | `dict` | 列名 `metadata`，属性名 `chunk_metadata`（避免与 `Declarative` 保留名冲突，`models/knowledge.py:22`） |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

索引：
- `ix_knowledge_chunks_source (source)` — 支撑 `list_chunks(source=...)`/`search(source=...)` 按来源过滤。

### knowledge_sources

对应实体：`KnowledgeSourceRow`（`models/knowledge.py:28`），迁移 `0006_knowledge_sources.py`（revision `b8c9d0e1f2a3`）。工作台创建的托管知识源元数据；代码种子源（project-docs/product-faq）不入此表。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| name (String(128), unique) | name (str) | ✓ | — | 知识源名，即 Agent YAML `knowledge.sources` 引用的 key |
| description (Text) | description (str) | ✓ | `""` | 描述 |
| chunk_size (Integer) | chunk_size (int) | ✓ | `500` | 切块参数 |
| overlap (Integer) | overlap (int) | ✓ | `50` | 切块参数 |
| files (JSON) | files (list[dict]) | ✓ | `list` | 落盘文件清单 `[{"filename", "size"}]`（重新切块按目录重读） |
| chunk_count (Integer) | chunk_count (int) | ✓ | `0` | 当前块数 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| updated_at (DateTime(tz)) | updated_at (datetime) | ✓ | `func.now()`（server_default + onupdate） | 更新时间，create/rechunk 后自动刷新 |

索引：主键 `id`；`name` 唯一约束。

### session_messages

对应实体：`SessionMessageRow`（`models/memory.py:11`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer, autoincrement) | id (int) | ✓ | — | 主键，消息序号（`upto_message_id` 依赖其单调递增） |
| session_id (String(128)) | session_id (str) | ✓ | — | 会话 id |
| run_id (String(36)) | run_id (str \| None) | — | NULL | 关联触发该消息的 Run（非外键） |
| role (String(16)) | role (str) | ✓ | — | 取值 `user`/`assistant`/`system`/`tool`（`memory/session.py:19-26` 显式校验，未知角色抛 `INTERNAL_ERROR`） |
| content (Text) | content (str) | ✓ | — | 消息内容 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

不寄生 `runs` 表：`runs` 是执行记录，消息是对话事实（`models/memory.py:12` 注释）。

索引：
- `ix_session_messages_session_id_id (session_id, id)` — 支撑 `history`/`_fetch_window` 按会话取最新 N 条（`ORDER BY id DESC LIMIT`）。

### session_summaries

对应实体：`SessionSummaryRow`（`models/memory.py:28`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer, autoincrement) | id (int) | ✓ | — | 主键 |
| session_id (String(128)) | session_id (str) | ✓ | — | 唯一键，每会话一行 |
| summary (Text) | summary (str) | ✓ | — | 摘要全文 |
| upto_message_id (Integer) | upto_message_id (int) | ✓ | — | 标记已压缩到的 `session_messages.id`（消息不物理删除，`models/memory.py:29` 注释） |
| updated_at (DateTime(tz)) | updated_at (datetime) | ✓ | `func.now()`（server_default + onupdate） | 更新时间，每次摘要压缩重写时自动刷新；本表无 `created_at`（`models/memory.py:37-39`） |

索引：
- `session_id` 唯一约束（`mapped_column(..., unique=True)`，`models/memory.py:34`）。

### long_term_memories

对应实体：`LongTermMemoryRow`（`models/memory.py:42`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer, autoincrement) | id (int) | ✓ | — | 主键 |
| agent_key (String(128)) | agent_key (str) | ✓ | — | 所属 Agent |
| user_key (String(128)) | user_key (str) | ✓ | — | 所属用户，与 `session_id` 无关（跨会话可 recall） |
| fact (Text) | fact (str) | ✓ | — | 事实文本，显式写入（无自动抽取） |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |

索引：
- `ix_long_term_memories_agent_key (agent_key)`、`ix_long_term_memories_user_key (user_key)` — 支撑 `recall(agent_key, user_key)` 的等值过滤（迁移 `0003` 分别建两个单列索引，非联合索引；`forget` 的按 id+agent_key+user_key 删除同样受益于此二索引，`models/memory.py:48-49`）。

### approvals

对应实体：`ApprovalRow`（`models/approval.py:18`），迁移 `0010_approval_records.py`（revision `f5a6b7c8d9e0`，down_revision `e4f5a6b7c8d9`）。高风险工具人工确认记录（R11 遗留项修复）：一次审批一条，决定不随进程重启丢失、可审计；供 `DbApprovalStore` 读写，`ApprovalManager()` 缺省内存 store 时不经本表。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (String(36)) | id (str) | ✓ | — | 主键，`ApprovalManager.create_request` 生成 uuid4 hex |
| run_id (String(36)) | run_id (str) | ✓ | — | 发起审批的 Run（非外键）；索引 `ix_approvals_run_id` 支撑按 Run 过滤 |
| tool_key (String(128)) | tool_key (str) | ✓ | — | 触发审批的工具 key |
| args (JSON) | args (dict[str, Any]) | ✓ | `dict` | 审批时的工具参数快照 |
| status (String(32)) | status (str) | ✓ | `pending` | 枚举 `pending`/`approved`/`rejected`/`expired`，终态不可逆；索引 `ix_approvals_status` 支撑 `try_resolve` 条件更新与 `list_pending` |
| decided_by (String(128)) | decided_by (str \| None) | — | NULL | 终态归因（HTTP 路由写入已验证主体；自动批准为 `auto-approve`；过期为 NULL） |
| decided_at (DateTime(tz)) | decided_at (datetime \| None) | — | NULL | 终态时间 |
| created_at (DateTime(tz)) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间，`list_pending`/`list_all` 排序依据 |

索引：
- 主键 `id`；`ix_approvals_run_id (run_id)` 与 `ix_approvals_status (status)`（迁移 `0010` 建立）。

## 行为

### 状态机

**Run 状态机**（`RunStatus`，`lubanagent/core/run.py:13`）：

```
PENDING(pending)          初始态，create_run 落库时默认值
  │  RunRepository.update_run（调用方驱动状态流转，无独立 HTTP 路径直转）
  ▼
RUNNING(running)          运行中
  │
  ├──▶ WAITING_APPROVAL(waiting_approval)   等待人工审批（Tool 治理触发）
  │       │
  │       └──▶ RUNNING（审批通过后回到运行中）
  │
  ├──▶ COMPLETED(completed)   终态，end_reason 通常为 final_answer/step_limit/budget_exceeded
  ├──▶ FAILED(failed)         终态，end_reason=error，error 字段填充详情
  └──▶ CANCELLED(cancelled)   终态，end_reason=cancelled
```

三个终态（`COMPLETED`/`FAILED`/`CANCELLED`）均通过 `TraceWriter.finish_run`（`observability/trace/writer.py:95`）统一入口：调用 `RunRepository.update_run` 更新 `runs` 终态字段、`TraceRepository.finish_trace` 同步 `traces.status`，并按 `_TERMINAL_EVENT` 映射表写入对应的 `TraceEventType`（`RUN_COMPLETED`/`RUN_FAILED`/`RUN_CANCELLED`）。`finish_run` 若传入非终态 `Run` 会抛 `VALIDATION_ERROR`（`observability/trace/writer.py:100`）。persistence 层本身不含状态转换的业务校验（不做"只能从 X 转到 Y"的守卫），状态流转的合法性由上游 Runtime/Graph 层负责，persistence 只负责落库。

### 调用树

#### TraceWriter 双写（Run + Trace 全生命周期）

`TraceWriter`（`observability/trace/writer.py:25`）是 persistence 层 `runs`/`traces`/`trace_events`/`token_usages`/`cost_records` 五张表的统一写入口，被 Runtime/Graph 执行器持有；每次运行经历「开始 → 事件/用量/费用流水 → 结束」三阶段，均落内部 DB（作为评测/成本/工作台唯一数据源，不依赖外部后端）。

```
start_run(run)
├── 构造 Trace(id=f"trace-{run.id}", ...)
├── RunRepository(session).create_run(run)   # 校验 agent/version 存在，落 runs 行
├── TraceRepository(session).create_trace(trace)   # 落 traces 头行
└── TraceRepository(session).add_event(RUN_STARTED)   # 落首个事件

emit(event) / record_model_usage(...) / record_cost(...)
├── [event.run_id 未缓存] trace_repo.get_trace_by_run(run_id)  # 缓存未命中兜底查库
│   └── 查不到 → 抛 RUN_NOT_FOUND
├── TraceRepository.add_event(event)   # 落 trace_events 明细
├── RunRepository.add_token_usage(...)   # 落 token_usages 明细
└── RunRepository.add_cost_record(...)   # 落 cost_records 明细

finish_run(run)
├── [条件] run.status 不在 _TERMINAL_EVENT 映射中
│   └── 抛 VALIDATION_ERROR("finish_run 要求终态 Run")
├── RunRepository.update_run(run)   # 覆盖 runs 终态字段
├── TraceRepository.finish_trace(run.id, run.status, ended_at)   # 落 traces 终态
└── TraceRepository.add_event(终态事件, payload={end_reason/error})   # 落收尾事件
```

## 边界规则

- `AgentRepository.create_version` 对同一 `(agent_id, version)` 重复写入会触发数据库 `UniqueConstraint("agent_id", "version")` 冲突，捕获 `IntegrityError` 后先 `rollback()` 复位事务，再抛 `LubanError(ErrorCode.VALIDATION_ERROR)`，版本快照永不允许覆盖（来源：`repositories/agent.py:63-72`）。
- `PromptRepository.create_version` 与 Agent 同型：对同一 `(prompt_id, version)` 重复写入捕获 `IntegrityError` 后 `rollback()` 再抛 `VALIDATION_ERROR`，Prompt 版本快照永不允许覆盖；`prompt` 不存在抛 `PROMPT_NOT_FOUND`（来源：`repositories/prompt.py:63-82`）。
- `resolve_prompt_reference` 的引用解析只发生在落库前（`api/routes/agents.py` 创建 Agent/追加版本端点）：`prompt_key` 为 `None` 原样返回（存量内嵌路径零行为变化）；prompt 不存在抛 `PROMPT_NOT_FOUND`、钉住版本不存在抛 `PROMPT_VERSION_NOT_FOUND`。运行期只读 `agent_versions` 快照，不回查 `prompts` 表（来源：`repositories/prompt.py:141-192`）。
- Prompt 新版不自动作用于引用方：`resolve_prompt_reference` 在 `prompt_version` 非 `None` 时按钉住版解析（即使 `prompts.current_version` 已推进），整份 Agent 定义回传（含旧 `prompt_version`）发新版本会沿用旧 Prompt 内容；Agent 要吃 Prompt 当前版必须显式传 `prompt_version: null`（来源：`repositories/prompt.py:161-185`）。
- `ModelRepository.set_brain_mapping`/`BrainMappingRow` 对同一 `(env, profile)` 只允许一条记录，重复设置走 upsert 更新而非新增（来源：`repositories/model.py:52-65`、`models/model_center.py:36`）。
- `ModelRepository.upsert_model` 在插入与更新两条写入路径上均先调 `validate_model_connection`（`lubanagent/core/model.py:83-96`）：`api_key_env` 环境变量名格式、`base_url` 禁凭证/查询参数/片段、`params` 禁连接覆盖键/秘密键/`[REDACTED]` 占位值、`factory="openai_compat"` 时 `params` 键限 `_OPENAI_PARAMS` 白名单；非法定义拒绝写入（来源：`repositories/model.py:15-45`）。更新语义：请求 `base_url` 为空表示沿用旧值（列表接口不返回旧连接地址，`repositories/model.py:37-45`）。
- `ModelRepository._to_domain` 用 `ModelDefinition.model_construct` 构造领域对象、跳过 pydantic 校验器（来源：`repositories/model.py:102-114`）：历史脏记录（校验收紧前入库的自定义 `params` 等）仍可读取供管理界面安全展示，但执行必须经 `create_openai_compatible` 构造入口再次校验并拒绝，不会拿脱敏占位值调用（`validate_model_connection` 拒绝含 `[REDACTED]` 的 `params`，`lubanagent/model/openai_compat.py:27-35`）。
- `TraceWriter.finish_run` 拒绝对非终态（非 `COMPLETED`/`FAILED`/`CANCELLED`）的 `Run` 调用，命中 `_TERMINAL_EVENT` 映射缺失即抛 `ErrorCode.VALIDATION_ERROR`（来源：`observability/trace/writer.py:97-103`）。
- `TraceRepository.add_event`/`finish_trace` 要求对应 `run_id` 已存在 `traces` 头行，否则抛 `ErrorCode.RUN_NOT_FOUND`；`RunRepository.update_run` 要求 `runs.id` 已存在，否则同样抛 `RUN_NOT_FOUND`（来源：`repositories/trace.py:45`、`:82`、`repositories/run.py:78`）——即 Trace/事件明细必须依附已落库的 Run，不可孤立写入。
- `EvalRepository.save_report` 要求 `report.dataset_key` 对应的 `eval_datasets` 行已存在，否则抛 `ErrorCode.VALIDATION_ERROR`（数据集须先经 `create_dataset` 登记，来源：`repositories/eval.py:64-72`）。
- `LongTermMemory.forget` 删除条件同时限定 `id`+`agent_key`+`user_key` 三者匹配，防止跨 Agent/跨用户越权删除他人事实（来源：`memory/long_term.py:90-100`）。
- `LubanCheckpointer.aput_writes`/`put_writes` 恒为 no-op（无 writes 表），pending writes 不落库；这是有意的裁剪设计（仅支持单 interrupt 节点的 null-resume），并非缺陷（来源：`persistence/checkpoint.py:76-84`、类注释 `:36-42`）。
- `ApprovalRepository.try_resolve` 的原子性靠条件更新而非事务锁：`UPDATE approvals SET status=..., decided_by=..., decided_at=now() WHERE id=? AND status='pending'`，受影响行数=1 才返回 True（`repositories/approval.py:69-83`）——并发/跨进程双 decide 恰一个成功，后到者由 `ApprovalManager.decide` 抛 `VALIDATION_ERROR`；审批记录持久化不承诺 Run 自动恢复——普通 Run 已有逐步 checkpoint，但恢复须显式调 resume API（非终态且有 checkpoint 才可续跑），无自动重启恢复，审批闸门语义不随 resume 改变（归属 runtime 层边界）。
- `Database.session()` 上下文管理器：正常退出自动 `commit()`，抛异常自动 `rollback()` 后重新抛出（来源：`session.py:22-30`），调用方无需手动管理事务边界。

## 名词

| 术语 | 语义 |
|---|---|
| 大脑槽位（brain_profile） | `fast`/`smart`/`cheap`/`vision` 四个逻辑角色，`brain_mappings` 表把 `(env, profile)` 映射到具体 `models.key`；切换模型只需改这张表的映射行，不改代码不发版（`lubanagent/core/brain.py:6`）。 |
| 编排 Run / 子 Run | `runs.parent_run_id` 非空时表示该 Run 是某个父 Run（多 Agent 编排入口）的子 Run，与父 Run 共享预算继承、取消传播语义（架构 §10.6），persistence 层仅存储该外键关系，不做预算/取消的业务逻辑。 |
| 双写模型 | 观测数据的内部 DB 落库（`runs`/`traces`/`trace_events`/`token_usages`/`cost_records`，`TraceWriter` 负责）与外部后端外发（Langfuse/LangSmith 等，失败落 `export_failures`）并存的架构决策；内部 DB 恒为 source of truth，外发可关闭不影响核心功能（架构 §6.1）。 |
| 内容 hash（content_hash） | `SkillRepository.upsert` 用于判断 Skill 是否需要更新版本的依据：仅当新 `SkillPackage.content_hash` 与 `manifest` 中存量 hash 不同才推进 `version`/`manifest`，否则只更新 `name`/`enabled`（`repositories/skill.py:31-36`）。 |
| checkpoint_ns / checkpoint_id | LangGraph 检查点的命名空间与 uuid6 时间序 id；`checkpoint_id` 字典序倒序等价于时间倒序，`LubanCheckpointer` 借此不建时间列即可取“最新一条”（`persistence/checkpoint.py:142-143` 注释）。 |
| 线程 id（thread_id） | LangGraph 检查点的会话线程标识，普通 Agent Run 约定为 `run-{run_id}`——Run id 即线程 id，`stream_resume` 按同一 thread_id 取最新 checkpoint 续跑（runtime 层约定，persistence 只存该字符串，不解析格式）。 |
| upto_message_id | `session_summaries` 表标记"已压缩进摘要的消息边界"，消息本身不物理删除；`SummaryMemory` 每次只处理 `id > upto_message_id` 的增量消息（`models/memory.py:29`、`memory/summary.py:60`）。 |
| 快照落地（Prompt 双轨） | Agent 定义经 `prompt_key`/`prompt_version` 引用 Prompt 库时，`resolve_prompt_reference` 在落库前把解析后的 Prompt 全文写入 `instructions`、变量合并进 `prompt_variables`，随 `agent_versions.definition` 快照冻结；运行/回放只读快照，不回查 `prompts` 表（`repositories/prompt.py:141`）。 |
| 钉住版本（prompt_version） | `AgentDefinition.prompt_version` 记录解析时实际使用的 Prompt 版本；回传非空即钉住该版（Prompt 库后续发版不自动生效），显式传 `null` 才取当前版——"分配 Prompt 给 Agent"即走此路径（`repositories/prompt.py:161-185`）。 |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.core` | `AgentDefinition`/`AgentVersion`/`PromptDefinition`/`PromptVersion`/`Run`/`Trace`/`TraceEvent`/`TokenUsage`/`SkillPackage`/`ModelDefinition` 等 Pydantic 领域模型 + `AgentStatus`/`RunStatus`/`EndReason`/`BrainProfile`/`TraceEventType`/`ErrorCode`/`LubanError` 枚举与异常 | 仓储层方法入参/出参类型与错误码来源，persistence 不定义业务语义，只做序列化落库 |
| `lubanagent.config` | `Settings().database_url` / `Settings().sync_database_url` | `create_database(settings)` 建库连接；`alembic.ini` 注明 URL 由 `migrations/env.py` 从 `Settings` 读取，`DATABASE_URL` 环境变量可覆盖 |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent.observability.trace`（`TraceWriter`） | `RunRepository`/`TraceRepository` | 运行全生命周期双写 `runs`/`traces`/`trace_events`/`token_usages`/`cost_records`，作为评测/成本/工作台唯一数据源（架构 §6.1） |
| `lubanagent.observability.cost`（`report.py`） | `CostRepository` | 成本报表按 agent/session/day/model 聚合查询 |
| `lubanagent.observability.evals`（`runner.py`/`replay.py`） | `EvalRepository` | 评测数据集加载与结果落库（领域模型本体在 `lubanagent.core.evaluation`，本路径为 re-export 垫片）、录制重放 |
| `lubanagent.api.routes`（`agents.py`/`prompts.py`/`runs.py`/`traces.py`/`models.py`） | `AgentRepository`/`PromptRepository`/`resolve_prompt_reference`/`RunRepository`/`TraceRepository`/`ModelRepository` | REST/SSE 接口的直接数据来源；agents 创建/发版端点落库前经 `resolve_prompt_reference` 解析 Prompt 引用（`api/routes/agents.py:28,59`） |
| `lubanagent.knowledge`（`store.py`/`manager.py`） | 直接 ORM 访问 `KnowledgeChunkRow`/`KnowledgeSourceRow` | 知识切块的增删查与向量检索；工作台托管知识源元数据（knowledge_sources 表）的读写 |
| `lubanagent.memory`（`session.py`/`summary.py`/`long_term.py`） | 直接 ORM 访问 `SessionMessageRow`/`SessionSummaryRow`/`LongTermMemoryRow` | 会话记忆、摘要压缩、长期记忆读写 |
| `lubanagent.graph` / `lubanagent.runtime` | `LubanCheckpointer`（直接 ORM 访问 `CheckpointRow`） | LangGraph 状态检查点落库：手写图与多 Agent 编排用 `graph.checkpoint`（re-export 垫片），普通 Agent Run 由 `AgentRuntime(checkpointer=...)` 注入同一实例、在节点边界逐步落 `checkpoints` 表（`thread_id=run-{run_id}`），支撑崩溃后 `stream_resume` 续跑 |
| `lubanagent.skill`（`registry.py`） | `SkillRepository` | Skill 能力包注册与版本管理 |
| `lubanagent.tool` / `lubanagent.app` | `DbApprovalStore`（实现 `ApprovalStore` 协议） | 审批记录落库与跨进程恢复：`app/factory.py:99` 注入 `ApprovalManager(store=DbApprovalStore(db))`，决定不随进程重启丢失；tool 层只认协议不 import persistence（依赖方向单向） |
| `lubanagent.mcp`（`registry.py`） | `McpServerRepository` | MCP Server 配置注册 |
| `lubanagent.model`（`brain_resolver.py`） | `ModelRepository` | 大脑槽位到具体模型的运行期解析 |
| `lubanagent.runtime`（`agent_loop.py`） | `RunRepository`（经 `TraceWriter`）/`ExportFailureRepository` | Agent 执行循环驱动 Run 落库；观测外发失败留痕写入 `export_failures`；`stream_resume` 调 `RunRepository.run_usage` 以账本实耗量重建 `BudgetGuard`（预算不重复放水） |
| `lubanagent.multiagent`（`team.py`） | `RunRepository`（经父子 Run 关系） | 编排 Run 的子 Run 归属与状态管理 |
| `lubanagent.app`（`factory.py`/`lifespan.py`） | `create_database(settings)` | 应用启动时建库、关闭时释放连接 |
| `lubanagent.cli`（`main.py`） | 多个 Repository | `luban migrate`/`luban eval`/`luban export` 等命令行操作 |
