# 契约 D1-1：`lubanagent/persistence` DB 模型 + 仓储 + Alembic

> 来源：开发计划 D1-1、架构设计 §2/§3.3、技术方案 §5.1/§5.2/§5.3、AGENTS.md §3（MySQL 8.0+ / 开发期 SQLite）。
> 前置：D0-1（core 领域模型）、D0-3（config.settings）已完成。

## 范围

- `lubanagent/persistence/`：`base.py`、`session.py`、`models/`（按域拆文件）、`repositories/`、`__init__.py`
- 仓库根：`alembic.ini` + `migrations/`（`env.py`、`script.py.mako`、`versions/0001_initial_schema.py`）
- `tests/lubanagent/persistence/`

**契约决策**：Alembic 落在仓库根 `migrations/`（架构树顶层"migrations/ = DB schema 迁移入口"），不在 `persistence/` 下重复建 migrations 目录（2026-07-23）。

## 数据契约（表结构）

SQLAlchemy 2.0 Declarative（`Mapped[]` + `mapped_column`），全部表含 `created_at`（`DateTime(timezone=True)`，server_default=func.now()）。JSON 列用 `sa.JSON`（SQLite/MySQL 通用）。字符串键/枚举列给明确长度（MySQL 索引要求）。枚举值与 core 逐字一致。

| 表 | 字段（除 created_at） | 约束/索引 |
| --- | --- | --- |
| `agents` | `id` int pk、`key` str(128)、`name` str(256)、`description` text、`current_version` int default 1、`status` str(32)（AgentStatus 值）、`updated_at` DateTime onupdate now | `key` unique |
| `agent_versions` | `id` int pk、`agent_id` int FK→agents.id、`version` int、`definition` JSON（AgentDefinition dump）、`status` str(32)、`published_at` DateTime nullable | unique(`agent_id`,`version`) |
| `runs` | `id` str(36) pk（uuid）、`agent_id` int FK、`agent_version_id` int FK→agent_versions.id、`session_id` str(128) nullable、`parent_run_id` str(36) nullable、`status` str(32)（RunStatus 值）、`input` JSON、`output` text nullable、`brain_profile` str(16)、`model_key` str(128) nullable、`end_reason` str(32) nullable、`token_usage` JSON、`cost` float default 0、`error` JSON nullable、`started_at`/`ended_at` DateTime nullable | index(`agent_id`)、index(`session_id`) |
| `traces` | `id` str(36) pk、`run_id` str(36) FK→runs.id、`status` str(32)、`started_at`/`ended_at` DateTime nullable | `run_id` unique |
| `trace_events` | `id` str(36) pk、`trace_id` str(36) FK→traces.id、`run_id` str(36)、`step_index` int nullable、`type` str(64)（TraceEventType 值）、`payload` JSON | index(`run_id`) |
| `token_usages` | `id` int pk、`run_id` str(36) FK、`step_index` int nullable、`model_key` str(128)、`input_tokens` int、`output_tokens` int | index(`run_id`) |
| `cost_records` | `id` int pk、`run_id` str(36) FK、`model_key` str(128)、`cost` float、`currency` str(8) default "CNY" | index(`run_id`) |
| `models` | `id` int pk、`key` str(128)、`provider` str(64)、`model_name` str(256)、`factory` str(64)、`capability` JSON、`params` JSON、`base_url` str(512) nullable、`api_key_env` str(128) nullable、`enabled` bool default True、`updated_at` onupdate now | `key` unique |
| `brain_mappings` | `id` int pk、`env` str(32)、`profile` str(16)（BrainProfile 值）、`model_key` str(128) FK→models.key、`updated_at` onupdate now | unique(`env`,`profile`) |
| `skills` | `id` int pk、`key` str(128)、`name` str(256)、`version` str(32)、`manifest` JSON、`enabled` bool default True | `key` unique |
| `mcp_servers` | `id` int pk、`key` str(128)、`name` str(256)、`transport` str(16)（`"stdio"`/`"http"`）、`config` JSON、`enabled` bool default True | `key` unique |
| `eval_datasets` | `id` int pk、`key` str(128)、`name` str(256)、`version` str(32)、`cases` JSON | `key` unique |
| `eval_results` | `id` int pk、`dataset_id` int FK→eval_datasets.id、`agent_key` str(128)、`agent_version` int、`model_key` str(128)、`score` float、`details` JSON | index(`dataset_id`) |
| `checkpoints` | `thread_id` str(128)、`checkpoint_ns` str(128) default ""、`checkpoint_id` str(128)、`parent_checkpoint_id` str(128) nullable、`type` str(64)、`checkpoint` sa.LargeBinary、`metadata` JSON | 复合 pk(`thread_id`,`checkpoint_ns`,`checkpoint_id`)（LangGraph checkpointer 落库布局，实现在 D3-4） |

文件拆分建议：`models/agent.py`（agents+agent_versions）、`models/run.py`（runs）、`models/trace.py`（traces+trace_events+token_usages+cost_records）、`models/model_center.py`（models+brain_mappings）、`models/misc.py`（skills+mcp_servers+eval_*）、`models/checkpoint.py`。`models/__init__.py` 全量 re-export。

## 接口契约

### base.py
- `Base = DeclarativeBase` 子类；`metadata` 供 Alembic `target_metadata` 使用。

### session.py
```python
class Database:
    def __init__(self, url: str) -> None          # 创建 AsyncEngine（pool_pre_ping=True；sqlite 时 connect_args={"check_same_thread": False}）
    @property
    def engine(self) -> AsyncEngine
    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]   # 提交/回滚/关闭语义：正常退出 commit，异常 rollback
    async def close(self) -> None
def create_database(settings: Settings) -> Database          # 从 settings.database_url
```

### repositories/（全部 async，返回 core 领域对象而非 ORM 对象）
每个仓储构造收 `AsyncSession`。ORM↔领域对象转换在仓储内完成。

- `AgentRepository`：`create_agent(definition: AgentDefinition) -> AgentDefinition`（同时落 agents 行 + version=definition.version 的 agent_versions 行）、`get_agent(key: str) -> AgentDefinition | None`（取 agents + 当前版本 definition 合成）、`create_version(agent_key: str, definition: AgentDefinition) -> None`、`get_version(agent_key: str, version: int) -> AgentVersion | None`、`list_agents() -> list[AgentDefinition]`、`update_status(agent_key: str, status: AgentStatus) -> None`
- `RunRepository`：`create_run(run: Run) -> None`、`get_run(run_id: str) -> Run | None`、`update_run(run: Run) -> None`（按 id 覆盖状态/输出/结束原因/用量/费用/error/时间戳）、`list_runs(agent_key: str | None = None, *, session_id: str | None = None, limit: int = 50) -> list[Run]`、`add_token_usage(run_id, step_index, model_key, input_tokens, output_tokens) -> None`、`add_cost_record(run_id, model_key, cost, currency="CNY") -> None`
- `TraceRepository`：`create_trace(trace: Trace) -> None`、`get_trace_by_run(run_id: str) -> Trace | None`、`add_event(event: TraceEvent) -> None`、`list_events(run_id: str) -> list[TraceEvent]`（按 created_at、id 升序）、`finish_trace(run_id: str, status: RunStatus, ended_at: datetime) -> None`
- `ModelRepository`：`upsert_model(definition: ModelDefinition) -> None`、`get_model(key: str) -> ModelDefinition | None`、`list_models(*, enabled_only: bool = True) -> list[ModelDefinition]`、`set_brain_mapping(env: str, profile: BrainProfile, model_key: str) -> None`（不存在则插、存在则改）、`get_brain_mapping(env: str, profile: BrainProfile) -> ModelDefinition | None`（join models 取完整定义；映射指向的 model 不存在 → None）
- `SkillRepository`：`upsert(skill: SkillRow 字典) -> None`、`get(key) -> dict | None`、`list() -> list[dict]`（Skill 领域模型属 D3-3，先用 dict 承载，仓储签名标注 TODO(D3-3)）
- `McpServerRepository`：`upsert/get/list`（dict 承载，TODO(D1-6)）
- `EvalRepository`：`create_dataset(key, name, version, cases) -> None`、`get_dataset(key) -> dict | None`、`save_result(dataset_key, agent_key, agent_version, model_key, score, details) -> None`、`list_results(dataset_key) -> list[dict]`（TODO(D2-1)）

### migrations/
- 根 `alembic.ini`：`script_location = migrations`，URL 不写死——`migrations/env.py` 从 `Settings().sync_database_url` 读（允许 `DATABASE_URL` env 覆盖），`target_metadata = Base.metadata`。
- `versions/0001_initial_schema.py`：**用 `alembic revision --autogenerate` 从 models 生成后人工核对**，包含全部 14 张表。
- 种子里程碑不做（种子数据属 CLI `luban init`，D1-8）。

## 行为契约

1. 仓储层抹平 SQLite/MySQL 方言（代码强制：只用通用类型 + sa.JSON，不用方言专属类型）。
2. `Database.session()` 异常即 rollback（代码强制）。
3. `create_version` 对同 `(agent_key, version)` 重复插入 → IntegrityError 包装为 `LubanError(VALIDATION_ERROR)`（版本不可变、不覆盖）。
4. 仓储返回 core 领域对象，调用方不接触 ORM 对象（架构 §3.3"persistence 不含业务规则"的接口体现）。

## 边界契约

- persistence 只 import `lubanagent.core`/`lubanagent.config` + sqlalchemy/alembic/标准库；禁止 import api/runtime/model 等上层兄弟包（架构 §3.1 persistence 在底层）。
- 开发期 SQLite 免安装、生产 MySQL 8.0+（AGENTS.md §3）。

## 验收标准（须实跑）

1. `DATABASE_URL` 指向临时 SQLite 文件 → `alembic upgrade head` 成功；`alembic downgrade base` 成功再 upgrade 回来（迁移可逆冒烟）。
2. `pytest tests/lubanagent/persistence/` 全绿：每个仓储的 CRUD 各至少一个用例（SQLite + aiosqlite，tmp_path 隔离）；含版本化字段断言（`agent_versions.version`、`runs.agent_version_id`）。
3. 重复 `create_version` 抛 `LubanError(VALIDATION_ERROR)`。
4. `get_brain_mapping` 在映射改指向另一 model key 后返回新定义（D1-2 验收"改映射立即走新模型"的存储层支撑）。
5. `ruff check lubanagent/persistence migrations` 无错。
6. MySQL 的 `alembic upgrade head`：本环境无 MySQL，记入 CI（D1-9）验证项，本地以类型通用性（sa.JSON/定长字符串/无方言 SQL）保证，strict-validation 时作为已声明限制说明。

## 存疑项与决策

1. Alembic 落点（`persistence/migrations/` vs 顶层 `migrations/`）→ 顶层 `migrations/`（见范围节决策）。
2. `Skill`/`McpServer`/`Eval` 领域模型属于后续单元 → 仓储暂以 dict 承载并标 TODO，不臆造 core 模型。
3. checkpointer 表先行落 schema（本单元），checkpointer 实现属 D3-4。
4. `create_version` 是否推进 `agents.current_version` 契约原未定义 → 决策：**推进**（不推进则新版本永不可经 `get_agent` 到达，版本化失去意义；技术方案 §5.2"运行期可维护"语义）。2026-07-23 主 agent 采纳 worker 建议。
