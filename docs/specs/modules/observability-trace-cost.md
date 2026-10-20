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

# observability-trace-cost 模块

## 定位

本模块是 LubanAgent 观测体系中"内部 DB 双写主笔 + 成本治理 + 评测体系"三个子域的集合，覆盖 `lubanagent/observability/trace/`（`writer.py`）、`lubanagent/observability/cost/`（`advice.py`/`estimate.py`/`pricing.py`/`report.py`）、`lubanagent/observability/evals/`（`assertions.py`/`dataset.py`/`judge.py`/`replay.py`/`report.py`/`runner.py`）。核心职责：`TraceWriter` 把每次 Run 的生命周期事件、Token 用量、费用同步落内部 DB（`runs`/`traces`/`trace_events`/`token_usages`/`cost_records` 表），作为"评测（F6）、成本归因（F7）、工作台 Trace 查看（F10）的唯一数据源"（来源：`lubanagent/observability/trace/writer.py:1`、架构设计 §6.1）；成本子域做事前 Token/费用估算与事后归因报表；评测子域基于真实 `AgentRuntime` 运行结果做断言（工具/输出/judge/拒答四类，含 `expect_refusal` 拒答硬指标与 `wrong_answer_rate` 错答率）、LLM-judge、回归对比与录制重放。本模块不包含外部后端适配器（Langfuse/LangSmith/Noop/SafeBackend），那部分属于 `observability-adapters` 模块。

## 接口契约

### HTTP

无——本模块不直接暴露 HTTP 路由；`lubanagent/api/routes/traces.py`（`GET /v1/traces/{run_id}`）与 `lubanagent/api/routes/evals.py`（`GET /v1/evals` 列表 + `GET /v1/evals/{result_id}` 详情，读 `EvalRepository` 落库的报告）属于 `api-app-cli` 模块，消费本模块的 `TraceRepository`/评测能力，详见该模块文档。

### RPC / MQ / Cron

无。

### Python API（本模块以库函数/类形式对外提供能力，非网络接口）

#### `lubanagent.observability.trace.writer.TraceWriter`（来源：`lubanagent/observability/trace/writer.py:25-150`）

| 方法 | 签名 | 说明 | 错误情况 |
|---|---|---|---|
| `__init__` | `(self, db: Database) -> None` | 持有 `run_id -> trace_id` 内存缓存（查库兜底，非强一致依赖） | 无 |
| `start_run` | `async def start_run(self, run: Run) -> Trace` | 落 `runs` 行 + `traces` 行 + `RUN_STARTED` 事件，三者同一 `db.session()` 内完成 | 无显式异常（底层 DB 异常直接抛出） |
| `resume_run` | `async def resume_run(self, run: Run) -> None` | 续跑登记（`writer.py:48-62`）：`RunRepository.update_run` 把 `runs` 状态置回 `RUNNING`（行已存在，不重建）+ 按 `run_id` 反查原 `traces` 行（不存在抛 `RUN_NOT_FOUND`）+ 追加 `RUN_RESUMED`（payload `{"resumed": true}`）事件——**同一 trace 行内事件连续，不重建 trace、不重发 `RUN_STARTED`** | Trace 不存在 → `LubanError(ErrorCode.RUN_NOT_FOUND)`（`writer.py:56-58`） |
| `emit` | `async def emit(self, event: TraceEvent) -> None` | 落 `trace_events` 行；入库 payload 为脱敏审计副本（构造新 `TraceEvent`，`payload=redact_value(event.payload)`，不改调用方事件，`writer.py:54-61`）；`trace_id` 先查内存缓存，未命中则按 `run_id` 查库 | Trace 不存在（既未缓存也查不到）→ `LubanError(ErrorCode.RUN_NOT_FOUND)`（`writer.py:64-70`） |
| `record_model_usage` | `async def record_model_usage(self, run_id: str, *, step_index: int \| None, model_key: str, input_tokens: int, output_tokens: int) -> None` | 落一条 `token_usages` 明细行 | 无显式异常 |
| `record_cost` | `async def record_cost(self, run_id: str, *, model_key: str, cost: float, currency: str = "CNY") -> None` | 落一条 `cost_records` 明细行 | 无显式异常 |
| `finish_run` | `async def finish_run(self, run: Run) -> None` | 更新 `runs` 终态字段 + `traces` 终态（status/ended_at）+ 按 `run.status` 选择并落终态事件（`RUN_COMPLETED`/`RUN_FAILED`/`RUN_CANCELLED`） | `run.status` 非终态（不在 `_TERMINAL_EVENT` 映射中）→ `LubanError(ErrorCode.VALIDATION_ERROR)`（`writer.py:97-103`） |

#### `lubanagent.observability.cost.estimate`（来源：`lubanagent/observability/cost/estimate.py`）

| 函数 | 签名 | 说明 |
|---|---|---|
| `estimate_tokens` | `(definition: AgentDefinition, user_input: str, *, history_chars: int = 0) -> TokenEstimate` | 纯计算，无 IO；`input_tokens = ceil((渲染后 instructions 字符数 + user_input 字符数 + history_chars) / 4) + 绑定工具数 * 200`；`output_tokens` 固定 `512`（近似值，与 `ModelCapability.max_output` 无关，来源：`estimate.py:9-36`） |
| `estimate_cost` | `(definition: AgentDefinition, model: ModelDefinition, user_input: str, *, history_chars: int = 0) -> float \| None` | 先调 `estimate_tokens` 再调 `cost_for_usage`；模型缺价格参数时返回 `None`（`estimate.py:39-51`） |

#### `lubanagent.observability.cost.pricing`（来源：`lubanagent/observability/cost/pricing.py`）

| 函数 | 签名 | 说明 |
|---|---|---|
| `prices_of` | `(definition: ModelDefinition) -> dict[str, float] \| None` | 从 `ModelDefinition.params` 读取 `input_price_per_1k`/`output_price_per_1k`（元/1k tokens）；两键须齐全，否则返回 `None`（不是 0，语义为"未知"，`pricing.py:9-15`） |
| `cost_for_usage` | `(prices: dict[str, float] \| None, usage: TokenUsage) -> float \| None` | `prices` 为 `None` 时返回 `None`；否则 `input_tokens/1000*input_price + output_tokens/1000*output_price`（`pricing.py:18-24`） |

#### `lubanagent.observability.cost.report`（来源：`lubanagent/observability/cost/report.py`）

| 项 | 签名/字段 | 说明 |
|---|---|---|
| `CostReport`（`BaseModel`） | `by_agent: list[dict]`、`by_day: list[dict]`、`cost_by_model: list[dict]`、`total_cost: float \| None`、`unknown_pricing_models: list[str]` | 存在未知价格模型时 `total_cost` 为 `None`（不是部分求和，`report.py:16-23`） |
| `build_cost_report` | `async def build_cost_report(session: AsyncSession, *, days: int = 7) -> CostReport` | 调 `CostRepository` 三个聚合查询 + 逐模型查价格，未注册或缺价的模型键收进 `unknown_pricing_models`（`report.py:26-54`） |
| `RunTreeCostReport`（`BaseModel`，H11） | `run_id: str`、`own_cost: float`、`descendants_cost: float`、`total_cost: float`、`own_runs: int`、`descendant_runs: int` | Run 树成本归因明示口径：父自身与后代分开列，`total_cost = own_cost + descendants_cost`，不重复相加 |
| `build_run_tree_cost_report` | `async def build_run_tree_cost_report(session: AsyncSession, run_id: str) -> RunTreeCostReport` | 经 `CostRepository.run_tree_cost` 按 `parent_run_id` BFS 逐层归集（每层一次查询，不依赖方言递归 CTE）；Run 不存在 → `raise LubanError(ErrorCode.RUN_NOT_FOUND)` |

#### `lubanagent.observability.cost.advice`（来源：`lubanagent/observability/cost/advice.py`）

| 函数 | 签名 | 说明 |
|---|---|---|
| `cost_advice` | `(report: CostReport) -> list[str]` | 纯函数（无 IO），按三条固定规则从 `CostReport` 生成建议文案：① 某 agent `input_tokens/output_tokens > 10` → 提示检查上下文膨胀；② 某 agent 日均 runs（`row["runs"]/max(len(report.by_day),1)`）`> 100` 且使用了"高价模型"（输出价格已知且高于报表内已知最低输出价格）→ 提示评估 cheap 槽位；③ `unknown_pricing_models` 非空 → 提示补价格参数（`advice.py:9-43`） |

评测领域模型（`ExpectOutput`/`JudgeSpec`/`EvalCase`/`EvalDataset`/`AssertionVerdict`/`CaseResult`/`EvalReport`/`ComparisonReport`）本体已下沉 `lubanagent/core/evaluation.py`（R5 评测体系打通：persistence 仓储需要领域对象承载，分层契约禁止 persistence → observability）；`observability/evals` 各文件保留原路径 re-export 垫片，既有导入（含 CLI/测试）零改动（来源：`core/evaluation.py:1-7`、各 shim 文件 docstring）。下表签名/行为不变，来源标注原模块路径（re-export 垫片）。

#### `lubanagent.observability.evals.dataset`（来源：`lubanagent/observability/evals/dataset.py`，模型本体 `core/evaluation.py`）

| 项 | 签名/字段 | 说明 |
|---|---|---|
| `ExpectOutput`（`BaseModel`） | `contains: list[str]` | 输出断言：关键字全部出现（大小写不敏感）才 pass |
| `JudgeSpec`（`BaseModel`） | `criteria: str`、`pass_score: int = 4` | LLM-judge 配置，1-5 分，`>= pass_score` 判 pass |
| `EvalCase`（`BaseModel`） | `name`（YAML 键 `case`）、`input: str`、`expect_tools: list[str]`、`expect_tool_args: dict[str, dict]`、`expect_output: ExpectOutput \| None`、`judge: JudgeSpec \| None`、`expect_refusal: bool = False` | 四类断言（工具/输出/judge/拒答）均未配置时仅校验 Run 是否 `COMPLETED`；`expect_refusal=True` 表示本用例必须拒答（防错答硬指标，`core/evaluation.py:39-41`） |
| `EvalDataset`（`BaseModel`） | `key: str`、`name: str = ""`、`cases: list[EvalCase]` | `key` 缺省取文件名去后缀（`load_dataset` 内固定赋值，`EvalDataset.key` 字段本身无默认值） |
| `load_dataset` | `(path: Path) -> EvalDataset` | 加载 YAML（顶层须为用例列表）；解析失败统一包成 `LubanError(ErrorCode.VALIDATION_ERROR)`，不透出原始 `yaml.YAMLError`/`pydantic.ValidationError`（`dataset.py:24-50`） |

#### `lubanagent.observability.evals.assertions`（来源：`lubanagent/observability/evals/assertions.py`）

| 项 | 签名/字段 | 说明 |
|---|---|---|
| `AssertionVerdict`（`BaseModel`） | `kind: Literal["tool_call","output","judge","refusal"]`、`passed: bool`、`detail: str = ""`、`error: str \| None` | `error` 非空表示判定过程本身出错（中立，不计入 pass/fail 聚合，见 `runner.py:91`） |
| `check_tool_calls` | `(case: EvalCase, events: list[TraceEvent]) -> AssertionVerdict \| None` | `expect_tools`/`expect_tool_args` 均未配置 → `None`；否则从 `TOOL_CALL_STARTED` 事件的 `payload["tool"]`/`payload["args"]` 判定，`expect_tool_args` 用子集匹配（`_args_contains`，`assertions.py:24-56`） |
| `check_output` | `(case: EvalCase, output: str \| None) -> AssertionVerdict \| None` | `expect_output` 未配置 → `None`；否则关键字全部命中（小写比较）才 pass（`assertions.py:59-73`） |
| `check_refusal` | `(case: EvalCase, events: list[TraceEvent]) -> AssertionVerdict \| None` | `expect_refusal=False` → `None`；True → 事件流含 `KNOWLEDGE_NO_HIT` 即 pass，缺失即 fail（"应拒答未拒答（错答）：事件流无 knowledge_no_hit，模型在低置信检索下作答"）。判定依据是事件不是文本（确定性，不依赖 judge）（`assertions.py:76-97`） |

#### `lubanagent.observability.evals.judge`（来源：`lubanagent/observability/evals/judge.py`）

| 项 | 签名/字段 | 说明 |
|---|---|---|
| `LlmJudge` | `__init__(self, model: BaseChatModel)`；`async def score(self, *, criteria: str, input: str, output: str) -> tuple[int, str]` | 用固定系统提示词要求模型只输出 JSON `{"score": 1-5, "reason": "..."}`；输出非法 JSON、非 dict、或 `score` 不是 1-5 整数 → `LubanError(ErrorCode.VALIDATION_ERROR)`（`judge.py:24-74`） |
| `check_judge` | `async def check_judge(case: EvalCase, judge: LlmJudge \| None, output: str \| None) -> AssertionVerdict \| None` | `case.judge` 未配置 → `None`；`judge is None`（运行器未提供）→ 直接判 `passed=False`；`LlmJudge.score` 抛 `LubanError` → 捕获后返回带 `error` 字段的中立 verdict（`judge.py:77-103`） |

#### `lubanagent.observability.evals.replay`（来源：`lubanagent/observability/evals/replay.py`）

| 项 | 签名/字段 | 说明 |
|---|---|---|
| `REPLAY_MODEL_KEY` | `"replay-fake"` | 重放专用临时模型键 |
| `script_from_events` | `(events: list[TraceEvent]) -> list[Any]` | 按序遍历 `MODEL_CALL_COMPLETED` 事件，按 `payload` 是否含 `tool_calls` 生成脚本条目（工具调用优先，同时含文本时文本被舍弃，`replay.py:18-44`） |
| `replay_run` | `async def replay_run(run_id: str, *, db: Database, registry: Registry, settings: Settings) -> Run` | 读原 Run 输入 + Trace 事件 → 生成 script → 临时把原 Run 的 `brain_profile` 映射指向 `REPLAY_MODEL_KEY`（fake 模型）→ 用 `AgentRuntime` 重跑（`session_id`/`user_id` 对齐原 Run，重放 Run 可与原 Run 按同一会话/用户标签归组，`replay.py:74`）→ `finally` 块恢复原映射（`replay.py:47-85`） | 原 Run 不存在 → `LubanError(ErrorCode.RUN_NOT_FOUND)`（`replay.py:54-57`） |

#### `lubanagent.observability.evals.report`（来源：`lubanagent/observability/evals/report.py`，模型本体 `core/evaluation.py`）

| 项 | 签名/字段 | 说明 |
|---|---|---|
| `CaseResult`（`BaseModel`） | `case: str`、`passed: bool`、`verdicts: list[AssertionVerdict]`、`run_id: str`、`output: str \| None`、`error: dict \| None` | 单 case 结果 |
| `EvalReport`（`BaseModel`） | `dataset_key`、`agent_key`、`agent_version: int`、`model_key: str = ""`（本次评测实际命中的模型键，CLI 落库前回填，runner 本身不知道模型解析结果）、`results: list[CaseResult]`、`created_at`；属性 `pass_rate`、`wrong_answer_rate`；方法 `to_json()`/`from_json(data)` | `pass_rate` 空结果集返回 `0.0`（`core/evaluation.py:77-82`）；`wrong_answer_rate` 为防错答硬指标（目标 0）：应拒答用例（verdicts 含 `kind="refusal"`）中未拒答的比例，无应拒答用例返回 `0.0`（`core/evaluation.py:84-106`） |
| `ComparisonReport`（`BaseModel`） | `baseline: EvalReport`、`current: EvalReport`、`regressions: list[str]`、`improvements: list[str]`、`pass_rate_delta: float` | — |
| `compare_reports` | `(baseline: EvalReport, current: EvalReport) -> ComparisonReport` | 按用例名对齐：基线 pass 本次 fail → 记入 `regressions`；基线 fail 本次 pass → 记入 `improvements`；用例在两份报告中都存在才参与判定（`core/evaluation.py:124-144`） |
| `EvalReportSummary`（`BaseModel`） | `id`、`dataset_key`、`agent_key`、`agent_version`、`model_key`、`pass_rate`、`passed_cases`、`total_cases`、`wrong_answer_rate`、`created_at` | `eval_results` 行的列表视图（API `GET /v1/evals` 响应）：聚合指标 + 定位键，不含逐 case 明细（`core/evaluation.py:147-159`） |
| `EvalReportDetail`（`EvalReportSummary` 子类） | 摘要全部字段 + `results: list[CaseResult]` | 详情视图（API `GET /v1/evals/{result_id}` 响应 / 仓储回读，`core/evaluation.py:162-165`） |

#### `lubanagent.observability.evals.runner.EvalRunner`（来源：`lubanagent/observability/evals/runner.py`）

| 方法 | 签名 | 说明 |
|---|---|---|
| `__init__` | `(self, runtime: "AgentRuntime", *, judge: LlmJudge \| None = None) -> None` | `runtime` 类型仅 `TYPE_CHECKING` 导入，运行时懒导入避免循环依赖 |
| `run_dataset` | `async def run_dataset(self, agent_key: str, dataset: EvalDataset, *, version: int \| None = None) -> EvalReport` | 逐 case 跑 `AgentRuntime.run()`，单 case 异常不中断整个数据集（记 `error`、`passed=False`），最终 `agent_version` 取最后一次实际观测到的版本（`runner.py:40-55`） |

## 数据模型

### `traces`

对应实体：`lubanagent.core.trace.Trace`

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| `id`（String(36)） | `id`（str） | ✓ | — | 主键，`TraceWriter` 生成为 `f"trace-{run.id}"`（`writer.py:35`） |
| `run_id`（外键 `runs.id`，unique） | `run_id`（str） | ✓ | — | 一 Run 一 Trace（唯一约束，来源：`persistence/models/trace.py:18`） |
| `status`（String(32)） | `status`（`RunStatus`） | ✓ | — | 与 `runs.status` 独立存储，`finish_trace` 单独更新 |
| `created_at`（DateTime(tz)） | — | ✓ | `func.now()` | 行创建时间（DB 侧默认值，非领域字段） |
| `started_at`（DateTime(tz)，可空） | `started_at`（datetime） | — | `run.started_at` 或落库时刻 | — |
| `ended_at`（DateTime(tz)，可空） | `ended_at`（datetime） | — | — | `finish_trace` 写入 |

### `trace_events`

对应实体：`lubanagent.core.trace.TraceEvent`

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| `id`（String(36)） | `id`（str） | ✓ | — | 主键，`TraceWriter._event` 生成为 `f"evt-{uuid4().hex}"`（`writer.py:123`） |
| `trace_id`（外键 `traces.id`） | — | ✓ | — | `add_event` 按 `run_id` 反查 `TraceRow` 得到（领域模型 `TraceEvent` 本身不带 `trace_id` 字段） |
| `run_id`（String(36)，索引） | `run_id`（str） | ✓ | — | — |
| `step_index`（int，可空） | `step_index`（int \| None） | — | — | — |
| `type`（String(64)） | `type`（`TraceEventType`） | ✓ | — | 枚举值见"名词"章节 |
| `payload`（JSON） | `payload`（dict[str, Any]） | — | `{}` | 事件附加数据，各事件类型自定义键 |
| `created_at`（DateTime(tz)） | `created_at`（datetime） | ✓ | — | 领域侧显式赋值（非 DB `server_default`） |

索引：
- `run_id` 单列索引（`mapped_column(..., index=True)`，`trace.py:32`）— 支撑 `list_events(run_id)` 按 Run 拉取全量事件

### `token_usages`

对应实体：无独立领域类（`TraceWriter.record_model_usage` 的入参组合）

| DB 列（DB 类型） | 说明 |
|---|---|
| `id`（int，主键自增） | — |
| `run_id`（外键 `runs.id`，索引） | — |
| `step_index`（int，可空） | — |
| `model_key`（String(128)） | — |
| `input_tokens`（int，默认 0） | — |
| `output_tokens`（int，默认 0） | — |
| `created_at`（DateTime(tz)，`server_default=func.now()`） | — |

索引：
- `run_id` 单列索引 — 支撑 `CostRepository` 按 Run 关联聚合

### `cost_records`

对应实体：无独立领域类（`TraceWriter.record_cost` 的入参组合）

| DB 列（DB 类型） | 说明 |
|---|---|
| `id`（int，主键自增） | — |
| `run_id`（外键 `runs.id`，索引） | — |
| `model_key`（String(128)） | — |
| `cost`（float，默认 0.0） | — |
| `currency`（String(8)，默认 `"CNY"`） | — |
| `created_at`（DateTime(tz)，`server_default=func.now()`） | — |

索引：
- `run_id` 单列索引 — 支撑 `CostRepository.cost_rows` 按模型聚合

### `export_failures`

对应实体：无独立领域类（`ExportFailureRepository` 直接操作 dict 视图）

| DB 列（DB 类型） | 说明 |
|---|---|
| `id`（int，主键自增） | — |
| `backend`（String(64)，索引） | 失败的外部后端名（如 `langfuse`/`langsmith`） |
| `run_id`（String(36)，非外键） | — |
| `event_type`（String(64)） | — |
| `payload`（JSON，默认 `{}`） | — |
| `error`（Text） | 错误信息文本 |
| `created_at`（DateTime(tz)，`server_default=func.now()`） | — |
| `retried_at`（DateTime(tz)，可空） | 最后一次重试尝试时间，`mark_retry_failed` 写入 |
| `retry_count`（Integer，默认 `0`，迁移 `0007` 新增） | 重试尝试失败次数；达上限（`DEFAULT_MAX_ATTEMPTS=3`）即放弃，行保留供运维查询 |

索引：
- `ix_export_failures_backend`（`backend`）— 支撑按后端筛选待重试记录

**归属说明**：`export_failures` 表定义在 `lubanagent/persistence/models/trace.py`（与 traces 系表同文件），但写入方是 `observability-adapters` 模块的 `SafeBackend`/`db_failure_sink`（见"边界规则"），本模块只共享该表定义、不直接写入。

### `eval_datasets` / `eval_results`（R5 已与本模块 evals 领域模型打通）

对应实体：`EvalDataset`/`EvalReport`/`EvalReportSummary`/`EvalReportDetail`（`lubanagent/core/evaluation.py`，经 `persistence/repositories/eval.py` 的 `EvalRepository` 读写，领域对象承载、不再走 dict）。

| 表 | 关键列 | 说明 |
|---|---|---|
| `eval_datasets` | `id`、`key`（unique）、`name`、`version`、`cases`（JSON）、`created_at` | 由 `luban eval run` CLI 命令落库登记（首次运行时 `create_dataset`），来源：`persistence/models/misc.py:41-51` |
| `eval_results` | `id`、`dataset_id`（外键 `eval_datasets.id`，索引）、`agent_key`、`agent_version`、`model_key`、`pass_rate`（float）、`passed_cases`/`total_cases`（int，默认 0）、`wrong_answer_rate`（float，默认 0.0）、`report`（JSON，完整 `EvalReport` 序列化）、`created_at` | `save_report` 写入（SkillRepository 同款全量 JSON 模式）：`report` 列存完整报告（逐 case pass/fail、断言明细、judge 结论、run_id/output/error），聚合列供列表页免解析查询；迁移 `0008` 把旧 `score`→`pass_rate`、`details`→`report` 并追加三个聚合列，旧行数据保留、聚合列 0 兜底（来源：`persistence/models/misc.py:54-73`、`migrations/versions/0008_eval_results_report_details.py`） |

打通后的完整链路：`luban eval run` → `EvalRunner`（本模块）产出 `EvalReport` → CLI 回填 `model_key` 后 `EvalRepository.save_report` 落库 → `GET /v1/evals`（`list_summaries`）/`GET /v1/evals/{result_id}`（`get_detail`）读真实数据 → 工作台评测页展示（列表 + 展开明细 + 断言四类）。`compare_reports` 回归对比仍走内存/JSON 文件基线链路，不读 DB（见"边界规则"）。

## 行为

### 状态机

无（Run 状态机归属 `runtime`/`core` 模块，本模块只负责按状态落库/落事件，不定义状态转换本身）。

### 调用树

#### Trace 写入：一次 Run 从建档到终态（调用方为 `runtime.AgentRuntime`/`graph.GraphRunner`，均持有各自的 `TraceWriter(db)` 实例，来源：`lubanagent/runtime/agent_loop.py:177`、`lubanagent/graph/runner.py:77`）

Run 开始时调用方构造 `Run(status=RUNNING, ...)` 并调 `start_run`；执行过程中每个可观测节点（模型调用、工具调用、预算事件等）调 `emit`；模型调用结束后额外调 `record_model_usage`；Run 结束时调 `finish_run`，若产生非零成本再调 `record_cost`（来源：`agent_loop.py:343-351,518-524,683-687`）。

```
TraceWriter.start_run(run)                      # runtime/agent_loop.py:343、graph/runner.py:208
├── RunRepository(session).create_run(run)      # 落 runs 行
├── TraceRepository(session).create_trace(trace)  # 落 traces 行，id = f"trace-{run.id}"
└── TraceRepository(session).add_event(RUN_STARTED)  # 落首条 trace_events

TraceWriter.resume_run(run)                      # runtime/agent_loop.py stream_resume 进入时调用
├── RunRepository(session).update_run(run)       # runs 状态置回 RUNNING（行已存在，不重建）
├── TraceRepository(session).get_trace_by_run(run.id)  # 沿用原 trace 行
│   └── [查不到] raise LubanError(RUN_NOT_FOUND)
└── TraceRepository(session).add_event(RUN_RESUMED, payload={"resumed": true})  # 同一 trace 追加，无第二条 RUN_STARTED

TraceWriter.emit(event)                          # 贯穿整个 Run 生命周期，逐事件调用
├── event = TraceEvent(..., payload=redact_value(event.payload), ...)  # 入库 payload 为脱敏审计副本，不改调用方事件（writer.py:54-61）
├── [event.run_id 未缓存] TraceRepository.get_trace_by_run(run_id)  # 缓存兜底查库
│   └── [查不到] raise LubanError(RUN_NOT_FOUND)
└── TraceRepository.add_event(event)             # 落一行 trace_events（脱敏后的审计副本）

TraceWriter.record_model_usage(run_id, ...)      # runtime/agent_loop.py:518，在 on_chat_model_end 回调内
└── RunRepository(session).add_token_usage(...)  # 落一行 token_usages（先落用量明细，再过预算闸）

TraceWriter.finish_run(run)                      # runtime/agent_loop.py:683、graph/runner.py:296
├── [run.status 非终态] raise LubanError(VALIDATION_ERROR)
├── RunRepository(session).update_run(run)       # 更新 runs 终态字段
├── TraceRepository(session).finish_trace(run.id, run.status, ended_at)  # 更新 traces 终态
└── TraceRepository(session).add_event(终态事件)  # RUN_COMPLETED/RUN_FAILED/RUN_CANCELLED（按 _TERMINAL_EVENT 映射选择）

TraceWriter.record_cost(run_id, ...)             # runtime/agent_loop.py:685，finish_run 之后、cost > 0 时才调
└── RunRepository(session).add_cost_record(...)  # 落一行 cost_records
```

#### 评测执行：`EvalRunner.run_dataset`（来源：`lubanagent/observability/evals/runner.py:40-100`）

由 `lubanagent/cli/main.py::luban eval run` 命令发起（详见 `api-app-cli` 模块文档）。逐条用例真实调用 `AgentRuntime.run()`（非 mock），跑完后读该 Run 的全量 Trace 事件做四类断言判定（工具/输出/拒答/judge），单条用例失败或异常不中断整个数据集运行。

```
EvalRunner.run_dataset(agent_key, dataset, version?)
├── for case in dataset.cases:
│   └── _run_case(agent_key, case, version)
│       ├── runtime.run(RunRequest(agent_key, case.input, version))  # 真实执行，非 mock
│       ├── [异常] → CaseResult(passed=False, error=_error_dict(exc))  # 不向上抛出，继续下一条 case
│       ├── [成功] TraceRepository(session).list_events(run.id)  # 读该 Run 全量事件
│       ├── check_tool_calls(case, events)   # 未配置 → None
│       ├── check_output(case, run.output)   # 未配置 → None
│       ├── check_refusal(case, events)      # expect_refusal=False → None；事件级确定性判定
│       ├── check_judge(case, judge, run.output)  # 未配置 judge → None；error verdict 不计 pass/fail
│       └── passed = run.status is COMPLETED and all(v.passed for v in effective_verdicts)
└── EvalReport(dataset_key, agent_key, agent_version, results=[...])
```

CLI 侧跑完后回填 `model_key` 并经 `EvalRepository.save_report` 把完整报告 + 聚合列落 `eval_results`（落库链路详见 api-app-cli 模块文档）；后续可调 `compare_reports(baseline, current)` 做回归/改进对比（按用例名对齐，见接口契约表；基线取内存/JSON 文件，不读 DB）。

#### 成本核算：事前估算与事后报表两条独立链路（无共享调用序列，均为纯函数/独立查询）

事前（Run 发起前，供 UI/CLI 展示预估成本，不落库）：

```
estimate_cost(definition, model, user_input, history_chars?)
├── estimate_tokens(definition, user_input, history_chars?)  # 纯字符数计算，无 IO
│   └── input_tokens = ceil(总字符数/4) + 工具数*200；output_tokens = 512（固定近似值）
├── prices_of(model)   # 读 ModelDefinition.params，缺任一价格键 → None
└── cost_for_usage(prices, usage)  # prices 为 None → 直接返回 None（不计 0）
```

事后（评测/工作台按需触发，读 DB 真实记录）：

```
build_cost_report(session, days=7)
├── CostRepository.aggregate_by_agent()   # join runs+token_usages+agents
├── CostRepository.aggregate_by_day(days) # 按 date(created_at) 分组
├── CostRepository.cost_rows()            # join cost_records+runs，按 model_key 聚合
├── _load_prices(session, model_keys)     # 查 ModelRow → prices_of()，未注册/缺价 → None
└── total_cost = None if 存在未知价格模型 else sum(cost_by_model[*].cost)

cost_advice(report)   # 纯函数，读 CostReport 三个规则判定，见接口契约表

build_run_tree_cost_report(session, run_id)   # H11 Run 树归因
└── CostRepository.run_tree_cost(run_id)      # own=本 Run cost_records 合计；
                                              # 后代=parent_run_id BFS 全部子孙各自合计；
                                              # total=own+descendants（不重复相加）
```

## 边界规则

- **`finish_run` 强制要求终态 Run**：`run.status` 不在 `{COMPLETED, FAILED, CANCELLED}` 时直接 `raise LubanError(ErrorCode.VALIDATION_ERROR)`，不允许把中间态 Run 标记为"结束"（来源：`lubanagent/observability/trace/writer.py:97-103`）。
- **`resume_run` 只追加不重建**：续跑沿用原 `traces` 行与事件流（`RUN_RESUMED` 追加在同一 trace 内，不重发 `RUN_STARTED`），Trace 连续性由同一 `trace_id` 保证；原 trace 不存在抛 `RUN_NOT_FOUND`（来源：`writer.py:48-62`）。
- **`emit` 依赖 Trace 先建档**：若事件的 `run_id` 既不在内存缓存也查不到 `TraceRow`，直接 `raise LubanError(ErrorCode.RUN_NOT_FOUND)`，即 `start_run` 必须先于任何 `emit` 调用完成（来源：`writer.py:64-70`）。
- **`emit` 入库 payload 是脱敏审计副本（H07）**：`TraceWriter.emit` 落库前构造新 `TraceEvent`、payload 经 `redact_value` 脱敏（秘密键/凭证形态 URL/bearer 字符串 → `[REDACTED]`），不改调用方原事件（`writer.py:48-61`）；即内部 DB 的 source of truth 保留的是可排查业务文本 + 脱敏秘密的审计副本。安全重放（`evals.replay`）遇脱敏记录（如含 `[REDACTED]` 的 `tool_calls.args`）须显式拒绝，不可当真实参数重放——该拒绝逻辑属 H10 待落地。
- **价格未知不得记 0，必须记 `None`**：`prices_of`/`cost_for_usage`/`build_cost_report.total_cost` 在价格缺失时统一返回/置 `None`，禁止用 `0` 掩盖"未知"语义，模块 docstring 显式钉死这条约束（来源：`pricing.py:1,18-24`、`report.py:44-47`）。
- **Run 树成本聚合不重复相加（H11）**：每个 Run 的 `cost_records`/`runs.cost` 只记自己模型的消费（本地归因在 runtime 侧代码强制），父自身（own）与全部后代（descendants）在 `run_tree_cost` 明示分开、`total = own + descendants`；不存在把父聚合值与子项再次相加的口径（来源：`repositories/cost.py::run_tree_cost`、`observability/cost/report.py::build_run_tree_cost_report`）。
- **evals 的 `error` verdict 不计入 pass/fail 聚合**：`AssertionVerdict.error` 非空（如 `check_judge` 解析失败）时该条判定被视为"中立"，从参与 `all(v.passed ...)` 聚合的 `effective` 列表中排除，不会因判定过程本身出错而误判 case 失败或成功（来源：`assertions.py:4-5`、`runner.py:88-90`）。
- **拒答断言（`expect_refusal`）是确定性判定，不依赖 judge**：判定依据是事件流是否含 `KNOWLEDGE_NO_HIT`（防错答守门短路时 `agent_loop` 必发），用例要求拒答而事件缺失即错答（fail）；`wrong_answer_rate` 是配套硬指标，目标 0（来源：`assertions.py:76-97`、`report.py:33-55`）。
- **`EvalRunner` 单 case 异常不中断整个数据集**：`_run_case` 用 `try/except Exception` 兜底，异常转成 `CaseResult(passed=False, error=...)` 后继续跑下一条用例（来源：`runner.py:57-78`）。
- **录制重放临时改写大脑映射、`finally` 保证恢复**：`replay_run` 用 `try/finally` 把 `brain_profile → REPLAY_MODEL_KEY` 的临时映射恢复为原模型键，即使重跑过程抛异常也会恢复，避免重放污染正常运行的模型路由（来源：`replay.py:62-83`）。
- **外部后端写失败不得阻断 Run（本模块的强制点仅止于"提供落表能力"，实际拦截逻辑在 `observability-adapters` 模块）**：`export_failures` 表定义在与 trace 系表同一文件 `persistence/models/trace.py`，本模块的 `ExportFailureRepository.record` 提供落库方法；但实际"外部后端 emit 失败 → 捕获异常 → 不阻断调用方 → 写 `export_failures`"这条降级链路的强制点在 `lubanagent/observability/adapters/base.py::SafeBackend`（`_emit_guarded`/`_emit_inline`/`_report_async` 方法，`adapters/base.py:114-178`），装配时由 `db_failure_sink(db)` 注入为 `SafeBackend` 的 `sink`（来源：`lubanagent/app/factory.py:58-61`）——**详见 `observability-adapters` 模块**，本模块不直接参与该降级判断。
- **`compare_reports`/回归判定不读 `eval_results` 表（JSON 是可移植基线载体，DB 定位持久化展示）**：R5 打通后 `EvalRepository` 已用 `EvalReport` 领域对象承载（`report` 列存完整报告 + 聚合列），CLI 落库与 API 读取走同一条链；但 `--compare` 的基线仍取内存 `EvalReport` 或 JSON 报告文件（`--save` 输出），回归对比语义不接 DB——JSON 文件是跨环境可移植的基线载体，DB 行是持久化展示与列表查询载体，二者分工有意分离（来源：`cli/main.py` eval 命令、`persistence/repositories/eval.py:64-86`）。

## 名词

| 术语 | 语义 |
|---|---|
| `TraceEventType` | `lubanagent.core.trace.TraceEventType`（`str, Enum`）定义的全部事件类型（19 种），本模块只是消费方（不定义枚举本身）：`RUN_STARTED`/`RUN_RESUMED`/`RUN_COMPLETED`/`RUN_FAILED`/`RUN_CANCELLED`/`STEP_STARTED`/`STEP_COMPLETED`/`MODEL_CALL_STARTED`/`MODEL_TOKEN_DELTA`/`MODEL_CALL_COMPLETED`/`TOOL_CALL_STARTED`/`TOOL_CALL_COMPLETED`/`TOOL_CALL_FAILED`/`TOOL_APPROVAL_REQUESTED`/`TOOL_APPROVAL_RESOLVED`/`BUDGET_WARNING`/`BUDGET_EXCEEDED`/`MODEL_FALLBACK`/`KNOWLEDGE_NO_HIT`（来源：`lubanagent/core/trace.py:12-31`；`RUN_RESUMED` 由 `TraceWriter.resume_run` 在 resume 续跑开始时落库，SSE 侧映射为 `run.resumed`） |
| 七问 | PRD/架构设计所称"每次 Run 可回答的七个问题"：Agent 版本/模型/Prompt/工具调用/Token/费用/结束原因；本模块的 `runs`/`token_usages`/`cost_records`/`trace_events` 四表组合是这七问的数据来源（来源：架构设计 §8「每次 Run 可回答 PRD 的七问」、`Run` 领域模型字段 `agent_version`/`model_key`/`end_reason`/`token_usage`/`cost`） |
| `source of truth` | 内部 DB（本模块四张核心表）是评测/成本/工作台 Trace 查看的唯一数据源，外部后端（Langfuse/LangSmith）只是可插拔的外发目标，不作为查询依据；自 H07 起落库的 `trace_events.payload` 是脱敏审计副本（秘密键/凭证形态字符串 → `[REDACTED]`），不是原始 payload 的逐字节复刻（来源：`writer.py:1,48-61`、架构设计 §6.1） |
| `export_failures` | 外部观测后端写失败的留痕表，重试消费方是 `observability-adapters` 模块 `SafeBackend` 的 drain 收尾回放（成功删/失败计次/默认 3 次达上限放弃）；表定义在本模块共享的 `persistence/models/trace.py`，但写入方属 `observability-adapters` 模块的 `SafeBackend` |
| 中立 error verdict | `AssertionVerdict.error` 非空时的判定结果：既不算通过也不算失败，从 pass/fail 聚合中排除，仅用于呈现"判定过程本身出错"这一事实（来源：`assertions.py:4-5`） |
| script（录制重放） | `script_from_events` 从历史 `MODEL_CALL_COMPLETED` 事件提取的模型输出序列，喂给 `ScriptedFakeChatModel`（`lubanagent.model.fake_definition`）做确定性重跑，条目为纯文本 `str` 或 `{"tool_calls": [...]}` 字典（来源：`replay.py:18-44`） |
| `agent_version` 的运行时取值 | `EvalRunner.run_dataset` 汇总报告的 `agent_version` 字段来自实际执行中观测到的最后一次 `run.agent_version`，若数据集为空或未取得任何值则回退为入参 `version or 0`（来源：`runner.py:44-49`） |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.persistence` | `TraceWriter`/`EvalRunner`/`build_cost_report` 全部方法内的 `db.session()` | `RunRepository`/`TraceRepository`/`CostRepository`（本模块内经 `lubanagent.persistence.repositories.cost` 导入）/`ModelRepository`（`replay.py`）读写 `runs`/`traces`/`trace_events`/`token_usages`/`cost_records` 表 |
| `lubanagent.core` | 全子域 | `Run`/`RunStatus`/`EndReason`/`Trace`/`TraceEvent`/`TraceEventType`/`TokenUsage`/`TokenEstimate`/`AgentDefinition`/`ModelDefinition`/`ErrorCode`/`LubanError` 等领域模型与错误体系 |
| `lubanagent.runtime` | `replay_run` 内懒导入 `AgentRuntime`/`RunRequest`；`EvalRunner` 持有的 `runtime` 参数（类型仅 `TYPE_CHECKING`） | 真实执行 Agent 得到 `Run` 结果与 Trace 事件，供评测断言/录制重放消费（懒导入避免 `runtime → observability → evals` 循环依赖，来源：`replay.py:58`、`runner.py:21-22,60`） |
| `lubanagent.extension` | `replay_run(registry=...)` | `Registry` 类型（重放时透传给 `AgentRuntime` 构造） |
| `lubanagent.config` | `replay_run(settings=...)` | `Settings`（读 `settings.env` 用于大脑映射查询/写入） |
| `lubanagent.model` | `replay.py` | `fake_definition()` 构造 `REPLAY_MODEL_KEY` 对应的 `ScriptedFakeChatModel` 定义 |
| `langchain_core` | `judge.py` | `BaseChatModel`/`HumanMessage`/`SystemMessage`（`LlmJudge` 包装 chat model 调用） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `api-app-cli` 模块（`lubanagent/api/routes/traces.py`、`evals.py`） | `GET /v1/traces/{run_id}`、`GET /v1/evals`、`GET /v1/evals/{result_id}` | 消费 `TraceRepository`（本模块共享的仓储）返回 `Trace` 头 + 全部 `TraceEvent`；evals 路由经 `EvalRepository` 读落库的 `EvalReport`（领域模型本体在本模块路径 re-export 的 `core/evaluation.py`） |
| `api-app-cli` 模块（`lubanagent/cli/main.py::luban eval run`） | 命令行调用 | 依赖本模块 `EvalDataset`/`EvalReport`/`EvalRunner`/`LlmJudge`/`compare_reports`/`load_dataset` 完成评测执行、报告生成与回归对比，跑完 `EvalRepository.save_report` 把完整 `EvalReport` + 聚合列落 `eval_results` |
| `observability-adapters` 模块（`SafeBackend`） | 未直接调用，共享 `export_failures` 表定义 | `SafeBackend` 失败降级链路把外发失败写入本模块所属 `persistence/models/trace.py` 中定义的 `ExportFailureRow`（经 `ExportFailureRepository`） |
| `runtime`/`graph` 模块 | `TraceWriter(db)` 实例化 + 全部方法调用 | `AgentRuntime`（`agent_loop.py`）与 `GraphRunner`（`graph/runner.py`）在 Run 生命周期各阶段调用 `start_run`/`resume_run`/`emit`/`record_model_usage`/`finish_run`/`record_cost`，是本模块 `TraceWriter` 的唯一调用方（`resume_run` 供 `stream_resume` 崩溃续跑登记） |
| 工作台前端（`lubanagent/ui`） | 经 `api-app-cli` 的 `/v1/traces/{run_id}` 间接消费 | Trace 查看页面数据来源最终落到本模块四张核心表 |
