# LubanAgent 检索纵深 / 记忆语义化 / 工具熔断 技术方案

> 状态：**DRAFT 待评审**。对标调研对象：HelloAgents（jjyaoao/HelloAgents，组件化多智能体框架）中四项工程能力，结合本仓库现状裁剪落地的设计。落地实现后须按文档影响闸门同步 PRD / 技术方案 / 架构设计 / 用户手册。
>
> 取证基线：`docs/specs/modules/knowledge-memory.md`、`docs/specs/modules/tool-mcp.md`、`lubanagent/knowledge/`、`lubanagent/memory/`、`lubanagent/tool/executor.py`、`lubanagent/core/evaluation.py`（2026-10-21 主干）。

## 0. 背景与动机

当前现状与差距（对照 HelloAgents）：

| 能力 | 本仓库现状 | 差距 |
|---|---|---|
| 知识检索 | 单路向量召回：向量存 DB JSON + Python cosine（`lubanagent/knowledge/store.py:search`），无关键词路、无重排 | 纯向量对中文短查询、专有名词、型号编号类查询召回质量差；无重排层 |
| 检索评测 | 工作台"检索测试"只能人工看 score 分布校准 `score_threshold`（`api/routes/knowledge.py` 的 `/v1/knowledge/query`） | 无 Recall@k / MRR / nDCG 等可量化指标，改切块参数、换 embedding 模型无法回归对比 |
| 长期记忆 | 全量注入：`LongTermMemory.recall` 取最近 20 条全量进 system suffix（`lubanagent/memory/long_term.py`） | 记忆量增长后全量注入撑爆上下文且不相关事实稀释注意力；无结构化用户画像 |
| 工具容错 | 超时 + 指数退避重试（`lubanagent/tool/executor.py`），仅幂等工具重试 | 外部工具持续故障时，每个 Agent Run 都会烧满重试预算；无熔断 |

四项均为运行期可选能力（默认关闭/直通，存量 Agent 零行为变化），符合"功能不分期、一等能力默认不启用"的范围政策。

## 1. 范围与 Non-goals

### 做什么（4 个工作包）

| 包 | 名称 | 内容 |
|---|---|---|
| **R1** | 混合检索 + 可插拔重排 | BM25 关键词召回与向量召回 RRF 融合（`retrieval_mode="hybrid"`）；`Reranker` Protocol 扩展点 |
| **R2** | 检索效果评测 | 查询集 + Recall@k / MRR / nDCG@k 指标，报告落库，工作台只读面板 |
| **M1** | 记忆语义化 + 用户画像 | 长期记忆向量召回 top-k 注入（`memory.semantic`）；`user_profile_entries` 结构化画像 + Agent 自维护工具 |
| **T1** | 工具熔断器 | 按工具 key 的 CLOSED→OPEN→HALF_OPEN 状态机，fail-fast，Trace 事件 |

### Non-goals（明确不做，及原因）

- **不引入专用向量库（Qdrant/Milvus）**：现有"向量存 DB JSON + Python cosine"决策在单源 ≤5000 块的配额下（`ingest.py:max_chunks=5000`）全量读可行；换向量库是数据基建决策，等真实规模压力出现再立项。
- **不做 GraphRAG**：实体关系抽取 + 社区报告是重 LLM 管线，与客服场景收益不匹配；如需，走用户领地自定义 `KnowledgeConnector` 即可，框架不内置。
- **不做 agentic RAG（检索作为工具）**：知识检索改造成 Agent 自主决策何时检索，改变 Run 事件流与守门语义，是方向性变化，另行立项；本期维持"Run 前一次性注入"模型。
- **不做多轮查询改写（query rewrite）**：依赖额外 LLM 调用，收益依赖场景，先不投入。
- **不做 BM25 的持久化倒排索引**：与 `search` 同模式按需全量读 + 内存建索引；5000 块规模下构建成本毫秒级，持久化索引是过度设计。
- **不内置任何厂商 reranker HTTP 客户端**：rerank API 无 OpenAI 兼容统一协议（Jina/Cohere/各厂商各异），框架只定 Protocol，真实实现由用户领地提供（与 `KnowledgeConnector` 用户扩展点同哲学）。
- **不做画像的 LLM 自动抽取**：finalize 时用模型从对话自动抽取画像不可靠且不可审计；画像写入走显式工具调用（确定性由代码执行），Agent 在对话中自主决定调用。
- **不做 per-tool 熔断参数**：`ToolContract` 级的阈值/冷却覆盖 v1 不做，全局 Settings 统一配置；确有 per-tool 需求时补 `ToolContract` 字段（向后兼容的追加）。
- **不做熔断状态持久化/跨进程共享**：熔断是进程内健康度信号，重启即清零是可接受语义（类比内存绑定表）。

## 2. 概念与可判定不变量

### 概念

- **双路召回（hybrid）**：同一查询，向量路（cosine top-N）与关键词路（BM25 top-N）各取候选，RRF（Reciprocal Rank Fusion，k=60）按排名融合为统一排序。
- **重排（rerank）**：融合候选（top_k×4 截断）交给 `Reranker` 重打分排序，再进守门过滤。
- **语义召回记忆（semantic recall）**：`recall` 以查询向量为序而非时间序取 top-k；无向量的事实按时间兜底补位。
- **用户画像（profile）**：`(agent_key, user_key, pkey) → pvalue` 的结构化键值对，唯一约束 upsert，Agent 经工具自主维护，prepare 全量注入。
- **熔断（circuit breaker）**：按工具 key 统计**连续**执行类失败，超阈值开路拒绝调用（fail-fast），冷却后半开探测。

### 不变量（每条可写成测试断言）

| # | 不变量 |
|---|---|
| I1 | `retrieval_mode` 缺省（`"vector"`）时，检索管线行为与本方案合入前逐字节一致（同输入同输出） |
| I2 | `memory.semantic=False`（默认）时，`prepare` 对长期记忆的注入内容与合入前一致 |
| I3 | `Reranker` 缺省（none）时为直通：输入列表顺序与输出列表顺序、元素完全相同 |
| I4 | `KnowledgeConfig.score_threshold > 0` 且 `retrieval_mode="hybrid"` 时，Run 失败并报 `VALIDATION_ERROR`（阈值语义不跨量纲混用，显式失败优于静默忽略） |
| I5 | 熔断器只统计 `RUN_TIMEOUT` 与 `TOOL_EXECUTION_FAILED` 两类错误；`VALIDATION_ERROR`/`TOOL_PERMISSION_DENIED`/`TOOL_APPROVAL_REQUIRED`/`TOOL_CIRCUIT_OPEN` 不改变计数 |
| I6 | 熔断 OPEN 状态下的拒绝调用 `attempts=0`，不进入执行、不触发重试、不触发审批 |
| I7 | 连续成功一次即从 HALF_OPEN 回 CLOSED；HALF_OPEN 中失败立即回 OPEN 并重置冷却计时 |
| I8 | `ToolCircuitBreaker` 缺省（None）时，`ToolExecutor.execute` 行为与合入前一致 |
| I9 | `LongTermMemory.recall` 在所有行均无 embedding 时，返回结果与现状（时间序 limit 条）完全一致 |
| I10 | 画像 upsert 对同一 `(agent_key, user_key, pkey)` 永远只有一行，后写覆盖先写 |
| I11 | 检索评测的三项指标对固定输入是确定性的（同查询集 + 同候选 → 同报告，无随机、无时钟依赖） |
| I12 | 本方案全部新表/新列均在一个新 alembic revision（down_revision=`'f5a6b7c8d9e0'`），所有新配置字段默认值 = 关闭或直通 |

## 3. 记录目录（数据模型，migration 0011）

### 3.1 long_term_memories 加列（存量表变更）

| 新列（DB 类型） | 实体字段 | 非空 | 默认 | 说明 |
|---|---|---|---|---|
| embedding (JSON) | embedding (list[float] \| None) | ✗ | NULL | 事实向量；NULL = 该行走时间序兜底 |
| embedding_model (String(128)) | embedding_model (str \| None) | ✗ | NULL | 产出向量的模型标识，维度/模型失配检测依据（教训来源：知识库换 embedding 模型旧向量失配须重灌） |

### 3.2 新表 user_profile_entries

| DB 列 | 类型 | 非空 | 默认 | 说明 |
|---|---|---|---|---|
| id | Integer, PK | ✓ | — | 主键 |
| agent_key | String(128), index | ✓ | — | 归属 Agent |
| user_key | String(128), index | ✓ | — | 归属用户 |
| pkey | String(128) | ✓ | — | 画像键（如 `preferred_channel`/`vip_level`） |
| pvalue | Text | ✓ | — | 画像值 |
| source_run_id | String(36) | ✗ | NULL | 写入该值的 Run（审计溯源） |
| updated_at | DateTime tz | ✓ | `func.now()` onupdate | 更新时间 |

约束：`UNIQUE(agent_key, user_key, pkey)`（I10）；建表 + 两索引 + long_term_memories 加列入同一个 alembic revision（down_revision `'f5a6b7c8d9e0'`，即 `0010_approval_records.py`——本仓库 0003 起 revision ID 为 12 位十六进制串，非文件序号），沿用 `migrations/versions/` 命名惯例，文件名 `0011_memory_semantic_profile.py`。

### 3.3 新表 retrieval_query_sets

| DB 列 | 类型 | 非空 | 说明 |
|---|---|---|---|
| id | Integer, PK | ✓ | 主键 |
| key | String(128), unique | ✓ | 查询集标识 |
| name | String(256) | ✓ | 展示名 |
| cases | JSON | ✓ | `list[RetrievalCase]` 序列化（SkillRepository 全量 JSON 模式） |
| created_at / updated_at | DateTime tz | ✓ | 同 knowledge_sources 惯例 |

### 3.4 新表 retrieval_eval_results

| DB 列 | 类型 | 非空 | 说明 |
|---|---|---|---|
| id | Integer, PK | ✓ | 主键 |
| query_set_key | String(128), index | ✓ | 引用查询集 key（不设 FK，删查询集不级联删历史报告） |
| retrieval_mode | String(16) | ✓ | `vector` / `hybrid` |
| rerank_backend | String(64) | ✓ | 报告生成时的重排后端名（快照，非外键） |
| k | Integer | ✓ | 指标截断位 |
| recall_at_k | Float | ✓ | 聚合均值 |
| mrr | Float | ✓ | 聚合均值 |
| ndcg_at_k | Float | ✓ | 聚合均值 |
| total_queries | Integer | ✓ | 查询数 |
| report | JSON | ✓ | 完整 `RetrievalEvalReport` 序列化（逐 query 明细） |
| created_at | DateTime tz | ✓ | 创建时间 |

### 3.5 Settings 新字段（全部默认关闭/直通，I12）

| 字段 | env | 默认 | 说明 |
|---|---|---|---|
| tool_circuit_failure_threshold | TOOL_CIRCUIT_FAILURE_THRESHOLD | 0 | 连续失败开路阈值；**0 = 禁用**（存量零行为变化） |
| tool_circuit_cooldown_seconds | TOOL_CIRCUIT_COOLDOWN_SECONDS | 30.0 | OPEN→HALF_OPEN 冷却时长 |
| rerank_backend | RERANK_BACKEND | "none" | 重排后端名；"none" = 直通；其他值在 prepare 检索时由 `reranker_resolver` 解析，未注册 → Run 失败 `CONFIG_ERROR`（显式失败，见 §5.3） |

（`KnowledgeConfig.retrieval_mode`、`MemoryConfig.semantic`、`MemoryConfig.profile` 为 pydantic 字段非 env，见 §4。）

## 4. 接口契约（完整签名；未实现方法显式拒绝）

### 4.1 R1 混合检索 + 重排

```python
# lubanagent/knowledge/bm25.py —— 新文件
def tokenize_for_bm25(text: str) -> list[str]:
    """ASCII 词元原样小写；CJK 连续段切字符 bigram（末尾单字符保留）。
    零第三方依赖（不引 jieba），bigram 对中文检索是工程上够用的基线。"""

class Bm25Index:
    def __init__(self, docs: list[tuple[int, str]]) -> None:
        """docs: (chunk_id, content)。构建期一次算好 df/idf，查询期只算 tf。"""
    def search(self, query: str, *, top_n: int) -> list[tuple[int, float]]: ...
```

```python
# lubanagent/knowledge/hybrid.py —— 新文件
def rrf_fuse(*ranked_lists: list[int], k: int = 60, top_k: int) -> list[tuple[int, float]]:
    """RRF 融合：score(d) = Σ_lists 1/(k + rank_i(d))。入参为各路 doc_id 按名次排列的列表。"""

class HybridRetriever:
    """BM25 + 向量双路召回，RRF 融合。实现 KnowledgeConnector（鸭子满足，不强制继承）。"""
    def __init__(self, source: str, *, store: KnowledgeStore, embedder: EmbeddingProvider,
                 bm25_top_n: int = 20, candidate_multiplier: int = 4) -> None: ...
    async def retrieve(self, query: str, top_k: int) -> list[Snippet]:
        """签名与 extension/protocols.py 的 KnowledgeConnector.retrieve 完全一致（位置参 top_k: int）。
        向量路：store.search(embedding, source=self._source, top_k=candidate_multiplier*top_k)（source 为 keyword-only）
        关键词路：store.list_chunks(self._source) 全量 → Bm25Index（每次调用重建，见 Non-goals）
        融合后取 top_k。Snippet.score = RRF 分数（量纲 ≠ cosine）。
        空库返回 []；embed 失败原样抛 LubanError（不降级单路）。"""
```

```python
# lubanagent/knowledge/rerank.py —— 新文件
class Reranker(Protocol):                      # @runtime_checkable
    async def rerank(self, query: str, snippets: list[Snippet], *, top_k: int) -> list[Snippet]: ...
class NoneReranker:
    """直通：snippets[:top_k] 原样返回（I3）。"""

# lubanagent/core/definition.py —— KnowledgeConfig 增字段
retrieval_mode: Literal["vector", "hybrid"] = "vector"   # 默认 = 现状（I1）
rerank_backend: str = "none"
```

装配链（`AgentRuntime` 构造，与 `summarizer`/`skill_registry` 注入同模式）：

```python
# lubanagent/runtime/agent_loop.py —— AgentRuntime.__init__ 增可选参数
retrieval_deps: RetrievalDeps | None = None
# RetrievalDeps（runtime/capabilities.py 定义）= dataclass(store, embedder, reranker_resolver)
# prepare 签名追加 keyword-only 参数 retrieval_deps=None；None 且配置了 hybrid/rerank → Run 失败 CONFIG_ERROR
```

`prepare` 检索段管线改为：

```
候选 = connector.retrieve / HybridRetriever.retrieve（按 retrieval_mode）
     → Reranker.rerank(query, 候选, top_k=definition.knowledge.top_k)     # rerank_backend ≠ none 时
     → _filter_snippets(守门) → 截断 top_k → 注入
```

### 4.2 R2 检索评测

```python
# lubanagent/knowledge/evaluation.py —— 新文件（纯库函数，无 DB 依赖，与 cost 模块同风格）
class RetrievalCase(BaseModel):
    query: str
    source: str | None = None            # None = 检索全部源（与 /v1/knowledge/query 同语义）
    relevant_any: list[str] = Field(min_length=1)   # 任一关键词出现在 snippet.content 即判"相关"

class RetrievalQuerySet(BaseModel):
    key: str; name: str = ""; cases: list[RetrievalCase]

class QueryMetrics(BaseModel):
    query: str; hits: int; recall_at_k: float; mrr: float; ndcg_at_k: float

class RetrievalEvalReport(BaseModel):
    query_set_key: str; retrieval_mode: str; rerank_backend: str; k: int
    per_query: list[QueryMetrics]
    recall_at_k: float; mrr: float; ndcg_at_k: float    # 聚合 = per_query 算术平均
    created_at: datetime

def judge_relevant(snippet: Snippet, case: RetrievalCase) -> bool:
    """case.relevant_any 任一关键词（大小写不敏感）是 snippet.content 子串 → True。"""

async def run_retrieval_eval(query_set, retrieve: Callable[[RetrievalCase, int], Awaitable[list[Snippet]]],
                             *, k: int = 5) -> RetrievalEvalReport: ...
    # MRR = 首个相关命中名次的倒数（无命中 = 0）
    # nDCG@k = DCG/discounted_ideal，二元 gain（相关=1，非相关=0）——I11 确定性
```

```python
# lubanagent/persistence/repositories/retrieval_eval.py —— 新文件（EvalRepository 同款全量 JSON 模式）
class RetrievalEvalRepository:
    async def save_query_set(self, qs: RetrievalQuerySet) -> None: ...     # key 重复 → VALIDATION_ERROR
    async def get_query_set(self, key: str) -> RetrievalQuerySet | None: ...
    async def list_query_sets(self) -> list[RetrievalQuerySetSummary]: ...
    async def save_report(self, report: RetrievalEvalReport) -> int: ...   # 查询集须已登记
    async def list_reports(self, query_set_key: str | None = None, *, limit: int = 20) -> list[RetrievalEvalReportSummary]: ...
    async def get_report(self, report_id: int) -> RetrievalEvalReport | None: ...
```

HTTP（挂 `/v1/knowledge`，经 `app.state` 鸭子类型访问，**不 import knowledge 模块**——既有分层禁忌）：

| 方法 | 路径 | 说明 | 错误 |
|---|---|---|---|
| POST | `/v1/knowledge/query-sets` | 登记查询集（201）；body `RetrievalQuerySet`；key 重复 → 409 | — |
| GET | `/v1/knowledge/query-sets` | 列出查询集（key/name/case 数） | — |
| POST | `/v1/knowledge/eval` | 跑检索评测（200）；body `{query_set_key, retrieval_mode="vector", k=5}`；`retrieval_mode="hybrid"` 时用 `HybridRetriever`；报告落库并返回全量 | 查询集不存在 → 404 |
| GET | `/v1/knowledge/evals` | 报告列表（聚合列） | — |
| GET | `/v1/knowledge/evals/{id}` | 报告全量（含逐 query 明细） | 不存在 → 404 |

CLI：`luban eval retrieval <query_set_key> [--mode vector|hybrid] [--k 5]`（独立进程装配三件套，R12 教训：CLI 路径必须走与 API 相同的完整装配）。

### 4.3 M1 记忆语义化 + 画像

```python
# lubanagent/memory/long_term.py —— LongTermMemory 变更
async def remember(self, agent_key: str, user_key: str, fact: str, *,
                   embedding: list[float] | None = None,
                   embedding_model: str | None = None) -> int: ...   # 可选参数，存量调用零变化
async def recall(self, agent_key: str, user_key: str, *, limit: int = 20,
                 query_embedding: list[float] | None = None,
                 query_embedding_model: str | None = None) -> list[str]: ...
    # query_embedding=None 或全部行无向量 → 时间序 limit 条（= 现状，I9）
    # 有向量：语义排序取前 limit；不足 limit 用无向量行按时间补位
    # 行向量维度 ≠ 查询维度 或 行 embedding_model ≠ query_embedding_model → 该行跳过语义排序、参与时间兜底（不炸、不误排）
```

```python
# lubanagent/memory/profile.py —— 新文件
class UserProfileStore:
    def __init__(self, db: Database) -> None: ...
    async def set(self, agent_key: str, user_key: str, pkey: str, pvalue: str,
                  *, run_id: str | None = None) -> None: ...            # upsert（I10）
    async def get(self, agent_key: str, user_key: str, *, pkey: str | None = None) -> list[ProfileEntry]: ...
    async def delete(self, agent_key: str, user_key: str, pkey: str) -> bool: ...

def format_profile_for_prompt(entries: list[ProfileEntry]) -> str:
    """空列表 → ""；否则 "用户画像：\n- pkey: pvalue" 逐行。"""
```

```python
# lubanagent/core/definition.py —— MemoryConfig 增字段
semantic: bool = False    # False = 现状全量注入（I2）
profile: bool = False     # False = 不注入画像、不注册画像工具
```

新增内置工具（经 `capabilities.prepare` 的 `extra_tools` 注册，`skill_load` 同模式；risk=MEDIUM，可配审批闸门）：

| 工具 key | args | 行为 |
|---|---|---|
| `memory_remember` | `{fact: string}` | `LongTermMemory.remember(agent_key, ctx.user_key, fact, embedding=embed(user_input 之外的事实文本), ...)`；`user_key` 为空 → 返回错误（同长期记忆注入前提） |
| `profile_set` | `{pkey: string, pvalue: string}` | `UserProfileStore.set(..., run_id=ctx.run_id)` |
| `profile_get` | `{pkey?: string}` | `UserProfileStore.get(...)`，序列化为 JSON 返回 |

工具仅在 `memory.semantic=True`（`memory_remember`）或 `memory.profile=True`（profile 两件）时注册；工具执行失败按 `TOOL_EXECUTION_FAILED` 收敛（走现有 executor 管线，含熔断）。

`prepare` 注入顺序（追加，不改既有顺序）：长期记忆（语义召回版）→ **画像**（新增，位于长期记忆之后、知识片段之前）→ 知识片段 → Skill 元数据。KV-cache 语义不变（动态后置）。

### 4.4 T1 工具熔断器

```python
# lubanagent/tool/circuit_breaker.py —— 新文件
CircuitState = Literal["closed", "open", "half_open"]

class ToolCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 30.0,
                 clock: Callable[[], float] | None = None,
                 on_transition: Callable[[str, CircuitState, CircuitState], Awaitable[None]] | None = None) -> None: ...
    def allow(self, key: str) -> bool:
        """closed → True；open 且冷却未到 → False（fail-fast）；open 且冷却已到 → 转 half_open、True；
        half_open → True（允许多探测并发，见竞态表）。同步方法，无 await（与 BudgetGuard 同纪律）。"""
    def record_success(self, key: str) -> None: ...
    def record_failure(self, key: str) -> None:
        """连续失败 +1；half_open 中失败立即回 open 并重置冷却（I7）。
        连续语义：任何 record_success 清零计数。"""
    def state(self, key: str) -> CircuitState: ...

# 错误码（lubanagent/core/errors.py 追加）
TOOL_CIRCUIT_OPEN = "TOOL_CIRCUIT_OPEN"

# Trace 事件（lubanagent/core/ 追加 TraceEventType）
TOOL_CIRCUIT_OPENED / TOOL_CIRCUIT_CLOSED
# executor 不持有 TraceWriter：状态迁移经 on_transition 回调上抛，agent_loop 订阅后 emit（与 approval 事件同路）

# lubanagent/tool/executor.py —— ToolExecutor.__init__ 增可选参数
circuit_breaker: ToolCircuitBreaker | None = None   # None = 禁用（I8）
```

`execute` 管线插入 step 0（一切校验/权限/审批之前）：

```
step 0: [breaker 非 None 且 not breaker.allow(descriptor.key)]
        → return ToolResult(success=False, error=TOOL_CIRCUIT_OPEN, attempts=0)   # I6
step 1-6: 现状不变（校验→权限→审批→执行→超时→重试）
收尾:   success → breaker.record_success(key)
        error.code ∈ {RUN_TIMEOUT, TOOL_EXECUTION_FAILED} → breaker.record_failure(key)   # I5
        其他错误码 / 熔断拒绝本身 → 不计数
装配:   app/factory.py 按 settings.tool_circuit_failure_threshold>0 构造 breaker 注入 executor；
        on_transition 回调在 agent_loop 侧 emit TOOL_CIRCUIT_OPENED/CLOSED 事件（写入 Trace 与 Run 事件流）
```

### 4.5 API 所有权表（包 ↔ 交付物；未实现前调用必须显式失败，禁止假装成功）

| 交付物 | 归属包 | 未实现时的行为 |
|---|---|---|
| `bm25.py` / `hybrid.py` / `rerank.py` / `retrieval_mode` | R1 | 字段值非 "vector"/"hybrid" → pydantic 校验失败（天然拒绝） |
| `evaluation.py` + 仓储 + 5 个 HTTP 端点 + CLI | R2 | 端点随包实现；实现前不注册路由（404，不假装成功） |
| migration 0011 + `LongTermMemory` 新签名 + `UserProfileStore` + 3 工具 + `semantic/profile` 字段 | M1 | `semantic=True` 但 `retrieval_deps=None` → Run 失败 `CONFIG_ERROR` |
| `circuit_breaker.py` + executor step 0 + 错误码 + 2 Trace 事件 + Settings | T1 | Settings 默认 0 = 不构造 breaker，路径不可达 |
| 工作台 KnowledgePage"检索评测"只读面板 | R2 | 本期只读展示报告列表/明细；查询集登记先走 API/CLI |

## 5. 行为与竞态

### 5.1 熔断状态机

```
            record_failure(连续 ≥ threshold)
  CLOSED ────────────────────────────────────► OPEN
     ▲                                          │ 冷却到期（allow 调用触发迁移）
     │            record_success                ▼
     └──────── HALF_OPEN ◄──────────────────  (open, expired)
                │
                └── record_failure → 回 OPEN，重置冷却计时
```

### 5.2 竞态清单（单事件循环；同步方法间无 await = 无竞态）

| # | 竞态 | 合法历史 | 强制机制 |
|---|---|---|---|
| C1 | allow 判定放行 → await 执行 → 期间他人触发开路 | 本请求照常执行完并计数；开路只影响后续调用 | 无锁；后果 = 多执行一次在途请求，可接受且不违反 I5-I7 |
| C2 | 冷却到期判定（allow 内）与 record_failure 并发 | 顺序只有两种：先迁移 half_open 再计失败（回 open）/ 先计失败（开路计时刷新）再迁移判定按新时间 | 同上，两种历史均语义正确 |
| C3 | HALF_OPEN 多请求并发探测 | 全部放行，成败各自计数；首个成功回 CLOSED | 显式契约：不保证单探测（写明，比隐蔽加锁诚实） |
| C4 | `memory_remember` 与容量淘汰并发 | 同 BudgetGuard 先例：consume 写入与淘汰在同一 DB 事务边界内，事件循环内串行 | DB 事务 |

### 5.3 hybrid 检索调用树（变更点）

```
capabilities.prepare(..., retrieval_deps)
└── [knowledge.retrieval_mode == "hybrid"]
    ├── [retrieval_deps is None] → raise LubanError(CONFIG_ERROR)          # 显式失败
    ├── [score_threshold > 0] → raise LubanError(VALIDATION_ERROR, "...量纲...")   # I4
    └── HybridRetriever(name, store=retrieval_deps.store, embedder=retrieval_deps.embedder)
        .retrieve(user_input, top_k)
        ├── embedder.embed([query])
        ├── store.search(vec, source=self._source, top_k=candidate_multiplier*top_k)  # 向量路（source 为 keyword-only）
        ├── Bm25Index([d["id"] for d in store.list_chunks(self._source)] 与 content).search(...)  # 关键词路（全量读建索引，limit 为 keyword-only 不传）
        └── rrf_fuse(...) → list[Snippet](score=RRF 分)
└── [rerank_backend ≠ "none"] retrieval_deps.reranker_resolver(backend)
    ├── 未注册该 backend → Run 失败 CONFIG_ERROR（显式失败优于静默降级，全方案统一口径）
    └── reranker.rerank(query, 候选, top_k)
└── _filter_snippets(守门) → 截断 → 注入   # 守门逻辑零改动
```

## 6. 测试分层（随包验收，逐条编码不变量）

| 层 | 覆盖 | 关键用例（示例断言） |
|---|---|---|
| 单元 | 指标/融合/状态机纯逻辑 | RRF 融合 gold case（两路排名手工算）；Recall/MRR/nDCG 对手工构造的候选序断言精确值（I11：跑两遍结果全等）；熔断状态机全转移覆盖（closed→open→half_open→closed、half_open 失败回 open 重置冷却，I7）；`NoneReranker` 直通（I3） |
| 行为 | 与现有组件接驳 | `retrieval_mode` 缺省时新旧检索输出逐条相等（I1）；`semantic=False` 注入字符串逐字节相等（I2）；breaker=None executor 全管线回归（I8）；无向量行 recall 与现状一致（I9）；错误码计数矩阵——五类错误各喂一次，断言仅两类推进计数（I5）；熔断拒绝 attempts=0 且不进审批（I6）；画像 upsert 两次同 key 仅一行（I10）；hybrid + score_threshold → Run 失败（I4） |
| 集成 | API/CLI/Run 端到端 | `POST /v1/knowledge/eval` 对 FakeEmbedding + 种子源返回可复算指标；`luban eval retrieval` CLI 独立进程出报告（R12 回归口径）；`memory_remember`/`profile_set` 工具经 executor 全管线（含审批拒绝路径）写入 DB 并在下一 Run 注入；migration 0011 在 SQLite 升降级往返后旧数据可读 |

每包验收 = 本包拥有的不变量全部有对应测试；默认配置回归（I1/I2/I8/I9/I12）是**合入门禁**，缺一不合入。

## 7. 实施顺序与依赖

```
T1（独立，最小：executor step0 + 状态机 + 事件）
 ├──────────────────────────────┐
R1（bm25 → hybrid → rerank → prepare 接线 + retrieval_deps 注入链）
 ├─► R2（依赖 R1 的 HybridRetriever 与 mode 才能评 hybrid；端点/CLI/面板随包）
M1（migration 0011 → LongTermMemory 语义召回 → UserProfileStore → 3 工具 → prepare 注入）
```

- T1 / R1 / M1 三包无相互依赖可并行；R2 必须在 R1 之后。
- 每包独立提交、独立走 strict-validation；全包完成后统一跑文档影响闸门：同步 PRD（能力清单）、技术方案（§对应章节）、架构设计（模块图）、用户手册（`website/manual/features/` 各一节）+ `docs/specs/modules/knowledge-memory.md`、`tool-mcp.md` 逆向同步。
- 风险登记：`docs/specs/risks.md` 追加——FakeEmbedding 下 hybrid/bm25 的指标绝对值无生产参考意义（零密钥演示口径，与 RAG 现状同边界）；BM25 全量重建索引在单源接近 5000 块时的延迟需实测（预估 <100ms，超预期则升级为源级索引缓存）。

## 8. 存疑项（已按技术性自主决策，评审时可推翻）

| 决策 | 理由 | 可推翻点 |
|---|---|---|
| BM25 中文切词用字符 bigram，不引 jieba | 零依赖、够用；jieba 是可选优化 | 若评测（R2）显示 bigram 召回显著差，可加 jieba optional extra |
| hybrid 下 `score_threshold` 直接拒绝 Run（I4）而非忽略 | 静默忽略阈值 = 守门失效不可见，违背"代码强制"哲学 | 可改为"hybrid 专属阈值字段"，但本期不值 |
| 熔断默认禁用（threshold=0） | 行为变化 opt-in，与守门三字段全默认零行为变化的兼容纪律一致 | 若认为基座应默认更稳，可改默认 5（一次性 breaking 决策，需用户确认） |
| 画像工具 risk=MEDIUM 可被审批闸门拦截 | Agent 自主写用户画像涉及合规，默认可拦但不强制审批 | — |
| nDCG 用二元相关性 | `relevant_any` 关键词判定天然二元，graded relevance 需要人工标注投入 | — |
