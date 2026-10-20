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

# knowledge-memory 模块

## 定位

`knowledge-memory` 模块合并两个运行期可选的能力层子包：`lubanagent/knowledge/`（最小 RAG 管线 + 防错答守门）与 `lubanagent/memory/`（会话/摘要/长期三种记忆策略）。两者都是框架领地的一等能力（非分期功能），默认不启用，由 `AgentDefinition.knowledge`/`AgentDefinition.memory` 显式配置开关（`lubanagent/core/definition.py:35-49`），并经 `lubanagent/runtime/capabilities.py` 的 `prepare`/`finalize` 装配进 Agent 执行主流程。知识侧的守门能力（"可不答、不硬编"）由 `KnowledgeConfig` 三个字段承载：`score_threshold`（相关度阈值过滤）、`no_hit_fallback`（全 miss 短路拒答）、`max_age_days`（知识时效过滤），全部带默认值、存量 Agent 零行为变化（`lubanagent/core/definition.py:39-42`）。核心表：`knowledge_chunks`（知识切块+向量）、`knowledge_sources`（工作台托管知识源元数据，migration 0006）、`session_messages`（会话消息）、`session_summaries`（会话摘要）、`long_term_memories`（跨会话事实）。核心 Protocol/类：`KnowledgeConnector`（`lubanagent/extension/protocols.py:87-89`）、`KnowledgeStore`（`lubanagent/knowledge/store.py:44`）、`EmbeddingProvider`（`lubanagent/knowledge/embedding.py:20`）、`KnowledgeSourceManager`（`lubanagent/knowledge/manager.py:67`，工作台托管知识源全生命周期：上传落盘/切块入库/注册/重切/删除/检索测试）、`SessionMemory`/`SummaryMemory`/`LongTermMemory`（`lubanagent/memory/session.py:34`、`lubanagent/memory/summary.py:59`、`lubanagent/memory/long_term.py:11`）。用户领地示例实现见 `app/knowledge_sources/product_faq.py` 与 `app/knowledge_sources/project_docs.py`（部门项目问答范本知识源）。

## 接口契约

### HTTP

工作台知识库管理路由（`lubanagent/api/routes/knowledge.py:22`，挂 `/v1` 前缀，路由聚合见 api-app-cli 模块文档）。路由层经 `request.app.state.knowledge_manager` 鸭子类型访问 `KnowledgeSourceManager`（`lubanagent/api/routes/knowledge.py:31-36`，不 import knowledge 模块——`api → knowledge` 是 import-linter 分层禁忌，同 skills 先例；manager 由 `lubanagent/app/factory.py:92-98` 装配挂载）。除这些管理端点外，知识检索能力本身经 `lubanagent/runtime/capabilities.py` 内嵌进 Agent Run 流程（Run 的 HTTP 入口见 runtime/api 模块文档）。

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/knowledge/sources` | 列出全部知识源：DB 托管行（managed=true）按 name 排序，再附加 registry 里不在 DB 的内置源（managed=false，保留注册顺序）；合并后未整体排序 | 无（`knowledge_manager` 未装配 → `LubanError(CONFIG_ERROR)`，`routes/knowledge.py:31-36`） |
| POST | `/v1/knowledge/sources` | multipart 上传多文件创建托管源（201）：`name`（Form，pattern `^[a-z0-9][a-z0-9_-]{1,63}$`）/`description`（默认 ""）/`chunk_size`（gt=0，默认 500）/`overlap`（ge=0，默认 50）/`files`（多文件，后缀白名单 `.md/.txt/.pdf/.docx/.csv/.json/.html/.xml`） | 重名 → 409 `conflict_response`（`routes/knowledge.py:68-69`）；超过 50 文件先拒绝；路由按 64 KiB 块累计，单文件 >10 MiB 或请求合计 >50 MiB 即停止读取，未调用 manager；坏后缀/overlap≥chunk_size/空文件列表由 manager 拒绝并映射为 422 `VALIDATION_ERROR`（缺少必填 `files` 字段则由 FastAPI 请求校验返回 422）（`routes/knowledge.py:25-29、72-110`；`manager.py:195-199、386-422`） |
| DELETE | `/v1/knowledge/sources/{name}` | 删除托管源（204）：块 + 落盘目录 + DB 行 + registry 注册全清 | 不存在 → 404 `KNOWLEDGE_SOURCE_NOT_FOUND`；非托管源（代码种子源）→ 422 `VALIDATION_ERROR`（`manager.py:350-364`） |
| POST | `/v1/knowledge/sources/{name}/rechunk` | 按新参数从落盘文件重新切块灌库（200），body `KnowledgeRechunkRequest{chunk_size, overlap}`，并 UPDATE 行的 chunk_size/overlap/chunk_count | 不存在 → 404；参数非法/落盘文件目录缺失 → 422（`manager.py:312-346`） |
| GET | `/v1/knowledge/sources/{name}/chunks` | 查看切块明细，query `limit`（1-1000，默认 200），返回 `list[KnowledgeChunkOut]`（无 embedding 字段） | 不存在 → 404 `KNOWLEDGE_SOURCE_NOT_FOUND`（`routes/knowledge.py:101-106`） |
| POST | `/v1/knowledge/query` | 检索测试（不经 Run/守门），body `{query(min_length=1), source|None, top_k(1-50, 默认 5)}`；source=None 检索全部源；返回 `list[KnowledgeHitOut]` 按 score 降序 | 未知源 → 422 `VALIDATION_ERROR`（`manager.py:166-177`） |

### RPC / MQ / Cron

无。

### Python API（模块间调用契约）

`lubanagent/extension/protocols.py:78-89`：

| Protocol/类 | 方法 | 说明 |
|---|---|---|
| `Snippet`（BaseModel） | 字段：`content: str`、`source: str`、`score: float = 0.0`、`metadata: dict = {}` | 知识检索片段（唯一定义处，`lubanagent/knowledge/connector.py` 仅 re-export） |
| `KnowledgeConnector`（`@runtime_checkable` Protocol） | `async def retrieve(self, query: str, top_k: int) -> list[Snippet]` | 知识源统一接口，用户扩展点 |

`lubanagent/knowledge/embedding.py`：

| 类 | 方法/签名 | 说明 |
|---|---|---|
| `EmbeddingProvider`（`@runtime_checkable` Protocol，L20） | `async def embed(self, texts: list[str]) -> list[list[float]]` | Embedding 抽象 |
| `OpenAICompatibleEmbedding`（L24） | `__init__(*, model, base_url=None, api_key_env=None)`；`async embed(texts)` | 懒加载 `langchain_openai.OpenAIEmbeddings`；`api_key_env` 未设置环境变量 → `LubanError(CONFIG_ERROR)`（L36-43）；未安装 `langchain_openai` → `LubanError(CONFIG_ERROR)`（L44-51） |
| `FakeEmbedding`（L61） | `__init__(*, dim: int = 2048)`；`async embed(texts)` | 确定性 token one-hot hash 向量（`_STOPWORDS` 高频功能字滤除，L14-16），零密钥兜底，"只服务测试与零密钥演示，不承诺生产检索质量"（L69 docstring）；两个去噪设计让 `score_threshold` 守门在文档级语料下可校准（L64-68 docstring）：① 默认 dim=2048——512 维在文档级语料（数百 chunk × 百级 token）下桶饱和，无关查询 cosine 虚高到 0.3+；② 功能字过滤——"怎么/什么/吗"类单字在任何中文语料都命中，口语化查询与无关文档的 cosine 被抬高。实测（五份项目文档 484 chunks）：正例查询 0.24-0.42、无关查询 0.13-0.235（上界 < 阈值 0.24） |

`lubanagent/knowledge/store.py`（`KnowledgeStore`）：

| 方法 | 签名 | 说明 |
|---|---|---|
| `source_transaction` | `async (source: str) -> AsyncIterator[AsyncSession]`（异步上下文管理器） | 同源写事务：SQLite 先 `BEGIN IMMEDIATE`，MySQL 在同一连接上 `GET_LOCK`/`RELEASE_LOCK`；异常回滚；MySQL 路径未做集成实测（`store.py:50-82`） |
| `write_chunks` | `async (source: str, chunks: list[dict], *, replace: bool, max_chunks: int, chunk_params: tuple[int, int] \| None = None, reservation_id: int \| None = None) -> int` | 同源事务内先用 `_check_reservation` 核对预留行：若有 `files=[]` 行，无 id 拒绝；传 id 时必须匹配该行 id 且仍待发布，否则 `CONFIG_ERROR`。随后复查块数/配额；replace 删旧块并写新块，传 `chunk_params` 时同事务更新托管行参数和 `chunk_count`；失败回滚本次 DB 写入（`store.py:84-131`） |
| `add_chunks` | `async (source: str, chunks: list[dict]) -> int` | 底层种子数据入口，无 `max_chunks` 配额检查；同源事务内拒绝 `files=[]` 预留源（`CONFIG_ERROR`），已发布托管行/无托管行仍可写；chunks 含 `chunk_index`/`content`/`embedding`/`metadata`，返回写入条数（`store.py:133-143`） |
| `delete_source` | `async (source: str) -> int` | 同源事务内拒绝 `files=[]` 预留源（`CONFIG_ERROR`）；其余按 source 整体删块并返回删除条数（`store.py:145-151`） |
| `list_chunks` | `async (source: str \| None = None, *, limit: int \| None = None) -> list[dict]` | 按 source、chunk_index 排序列出；`limit` 作用于排序后（L137-150）；dict 含 `created_at`（`_to_dict`，L26-35） |
| `count_chunks` | `async (source: str \| None = None) -> int` | 按 source（None=全表）计数（L152-157） |
| `search` | `async (embedding: list[float], *, source: str \| None = None, top_k: int = 5) -> list[dict]` | 全量读 + Python cosine 排序取 top_k，空库返回 `[]`（L168 docstring）；`top_k` 钳制到 `[1, MAX_SEARCH_TOP_K=100]`——0/负数按 1（原先会被切片静默吞掉全部结果），超大值按 100 截断，不抛错不吞结果（L38-41、165-198） |

`lubanagent/knowledge/ingest.py`：

| 函数/常量 | 签名 | 说明 |
|---|---|---|
| `SUPPORTED_SUFFIXES` | `frozenset({".md", ".txt", ".pdf", ".docx", ".csv", ".json", ".html", ".xml"})` | 工作台上传白名单；`.htm` 可由 `parse_document` 解析，但不属于上传白名单（`ingest.py:31-34、300-305`） |
| `parse_document` | `async (path: Path, *, max_input_bytes=10*1024*1024, max_text_chars=2_000_000, max_pdf_pages=200, zip_quota=_DOCX_QUOTA, parse_timeout=30.0) -> str` | 各格式入口限输入 10 MiB、提取文本 2,000,000 字符；PDF/DOCX 由父进程创建私有快照目录，子进程先以 64 KiB 有界读取（默认至多 10 MiB+1 字节用于判超额）复制快照再解析；启动、复制、解析共用 `parse_timeout` 截止时间（超时 `RUN_TIMEOUT`）；DOCX 解析前全成员核验 ZIP 配额；其余格式仍使用 `asyncio.to_thread` 有限读取/解析，不具备子进程强制终止语义；未知后缀 → `VALIDATION_ERROR`（`ingest.py:147-200、223-340`） |
| `chunk_text` | `(text: str, *, chunk_size: int = 500, overlap: int = 50, max_chunks: int \| None = None) -> list[str]` | 字符滑窗切块，段落边界优先断；可选块数上限在追加超额块之前拒绝；参数非法 → `VALIDATION_ERROR`（`ingest.py:329-357`） |
| `ingest_files` | `async (paths: list[Path], *, source: str, store: KnowledgeStore, embedder: EmbeddingProvider, chunk_size=500, overlap=50, replace=True, max_chunks=5000, chunk_params: tuple[int, int] \| None = None, reservation_id: int \| None = None) -> int` | replace=False 时先计已有块并以剩余额度切块；解析/切块后一次 embed，最终将可选 `reservation_id` 透传给 `store.write_chunks`，由同源事务核对预留所有权并复查现存块数/配额。replace=True 只按新块数限额；可传 `chunk_params` 同步更新托管行。metadata 含 filename/ingested_at（`ingest.py:360-404`） |
| `ingest_file` | `async (path: Path, *, source: str, store: KnowledgeStore, embedder: EmbeddingProvider, replace=True) -> int` | `ingest_files([path], ...)` 薄包装，签名不变（`ingest.py:405-416`） |

`lubanagent/knowledge/manager.py`（`KnowledgeSourceManager`，工作台托管知识源管理，L67-422）：

| 方法/常量 | 签名 | 说明 |
|---|---|---|
| `_SOURCE_NAME_RE` | `^[a-z0-9][a-z0-9_-]{1,63}$`（L28） | 源名规则（与 Agent YAML key 同风格，2-64 位小写字母/数字/-/_） |
| `MAX_FILE_BYTES` / `MAX_TOTAL_BYTES` | 10MB / 50MB（L29-30） | 单文件 / 单次上传总量上限 |
| `StoredFile`（L34）/ `SourceInfo`（L40） | dataclass | `StoredFile(filename, size)`；`SourceInfo(name, description, chunk_size|None, overlap|None, files, chunk_count, managed, created_at, updated_at)` |
| `ensure_restored` | `async () -> None`（L88） | 首次调用 `restore`，仅注册有非空 `files` 的行（跳过创建中的空行）和未注册的名字；`_restored` 标志避免重复执行；不负责遗留预留行/暂存目录自动清理（`manager.py:88-109`） |
| `restore` | `async () -> list[str]`（L95） | 同上但不置 `_restored` 标志，返回本次注册名列表（测试用） |
| `list_sources` | `async () -> list[SourceInfo]`（L116） | 仅展示 `files` 非空的 DB 托管行 ∪ registry-only 内置源名（后者 `managed=False`、`chunk_size/overlap=None`、`chunk_count` 现查 `store.count_chunks`）；空 `files` 预留行不可见（`manager.py:116-141`） |
| `get_source` | `async (name: str) -> SourceInfo \| None`（L143） | 有 `files` 的 DB 行优先，其次 registry-only；空 `files` 预留行不返回；都不在返回 None（`manager.py:143-161`） |
| `create_source` | `async (*, name: str, description: str, chunk_size: int, overlap: int, files: list[tuple[str, bytes]]) -> SourceInfo` | 校验后在同源写事务内排除同名行/registry、遗留目录/旧块；独立预留任务提交 `files=[]` 行并以 `asyncio.shield` 等待，取消时仍等待取得真正已提交的 `owned_id`；暂存文件后将此 id 作为 `reservation_id` 传给 `ingest_files`，仅当前待发布预留行可经 store 写块，再独占发布目录、按 id 回写清单/块数并注册。异常/取消仅在取得 id 且行仍待发布时按 source 清理块及自有行/目录；最终提交已发布后不回滚已发布数据，若在返回值前取消需查询确认结果；崩溃遗留不自动回收（`manager.py:181-308`）。manager 接收字节列表，不碰 `UploadFile` |
| `delete_source` | `async (name: str) -> None`（L300） | 仅托管源：`store.delete_source` + `shutil.rmtree` 落盘目录 + 删 DB 行 + `registry.unregister_knowledge` 全清 |
| `rechunk` | `async (name: str, *, chunk_size: int, overlap: int) -> SourceInfo`（L312） | 从落盘目录重读支持后缀的文件，调用 `ingest_files(..., replace=True, chunk_params=(chunk_size, overlap))`；旧块删除、新块写入及参数/块数更新同一 DB 事务，失败回滚旧块和旧参数；目录缺失/无白名单文件 → `VALIDATION_ERROR`（`manager.py:312-346`） |
| `list_chunks` | `async (name: str, *, limit: int) -> list[dict]`（L163） | 透传 `store.list_chunks(name, limit=limit)` |
| `search` | `async (source: str \| None, query: str, *, top_k: int) -> list[dict]`（L166） | 检索测试：embed 查询后按源过滤（None=全源）；未知源（不在 `list_sources` 结果）→ `VALIDATION_ERROR` |

Registry 侧配套（`lubanagent/extension/registry.py`）：新增幂等 `unregister_knowledge(name)`（L117-120，`self._knowledge.pop(name, None)`，未注册名静默跳过）——被删源若仍被 Agent YAML 引用，按既有设计 run 期报"知识源未注册"，不静默继续服务。

`lubanagent/knowledge/retrieve.py`：

| 类/函数 | 签名 | 说明 |
|---|---|---|
| `SqlKnowledgeConnector`（L8） | `__init__(source, *, store, embedder)`；`async retrieve(query, top_k) -> list[Snippet]` | 实现 `KnowledgeConnector`，基于 `knowledge_chunks` 表 + Python cosine |
| `format_snippets_for_prompt` | `(snippets: list[Snippet]) -> str` | 输出 `"[1] content...\n[2] ..."` 编号格式（L33） |

`lubanagent/memory/session.py`：

| 类/函数 | 签名 | 说明 |
|---|---|---|
| `row_to_message` | `(row: SessionMessageRow) -> BaseMessage` | role→langchain 消息类型映射；`tool` 角色的 `tool_call_id` 以空串占位（L18-26）；未知 role → `LubanError(INTERNAL_ERROR)`（L27-31） |
| `SessionMemory`（L34） | `__init__(db)`；`async append(session_id, role, content, *, run_id=None)`；`async history(session_id, *, window_size) -> list[BaseMessage]`；`async count(session_id) -> int`；`async clear(session_id) -> int` | 会话消息读写；`history` 返回最新 window_size 条按时间升序 |

`lubanagent/memory/summary.py`：

| 类 | 方法 | 说明 |
|---|---|---|
| `Summarizer`（`@runtime_checkable` Protocol，L23） | `async summarize(messages, *, previous: str \| None) -> str` | 摘要器协议 |
| `FakeSummarizer`（L29） | `__init__(*, max_chars=200)`；`async summarize(...)` | 确定性拼接截断，测试用，记录调用参数 `.calls` |
| `LlmSummarizer`（L45） | `__init__(model: BaseChatModel)`；`async summarize(...)` | 经 brain cheap 槽位模型摘要，模型由调用方注入 |
| `SummaryMemory`（L59） | `__init__(db, *, summarizer)`；`async get_summary(session_id) -> str \| None`；`async maybe_compress(session_id, *, window_size) -> bool`；`async context_prefix(session_id, *, window_size) -> list[BaseMessage]` | 压缩策略：未压缩消息数 > window_size 触发（L72-73） |

`lubanagent/memory/long_term.py`：

| 类/函数 | 方法/签名 | 说明 |
|---|---|---|
| `LongTermMemory`（L11） | `__init__(db, *, capacity: int \| None = None)`；`async remember(agent_key, user_key, fact) -> int`；`async recall(agent_key, user_key, *, limit=20) -> list[str]`；`async forget(agent_key, user_key, fact_id) -> bool` | 跨会话事实读写，与 `session_id` 无关（L12 docstring）；`forget` 限定 agent+user 防越权（L90）；容量控制（风险 R10）：`capacity` 缺省取 `Settings.long_term_memory_capacity`（默认 1000，env `LONG_TERM_MEMORY_CAPACITY`），`remember` 写入后同 agent+user 维度超限即淘汰最旧（`_evict_overflow`，L34-64，`created_at`/`id` 正序删 overflow 条）——长期运行表不再无限增长，存量不超上限时零感知 |
| `format_facts_for_prompt` | `(facts: list[str]) -> str` | 格式化 system prompt 尾部片段；空列表 → `""`（L61-63） |

## 数据模型

### knowledge_chunks

对应实体：`KnowledgeChunkRow`（`lubanagent/persistence/models/knowledge.py:13`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| source (String(128)) | source (str) | ✓ | — | 知识源名，对应 `KnowledgeConnector` 注册名 |
| chunk_index (Integer) | chunk_index (int) | ✓ | — | 块在文档内的序号 |
| content (Text) | content (str) | ✓ | — | 切块文本 |
| embedding (JSON) | embedding (list[float]) | ✓ | — | 向量存 DB JSON float 数组（决策：Python cosine 检索，非专用向量库） |
| metadata (JSON, 列名 metadata, 代码属性 chunk_metadata) | chunk_metadata (dict) | ✓ | `dict`（代码侧 default，见 store.py:22 `chunk.get("metadata") or {}`） | 附加元数据：`ingest_file` 写入 `{"filename", "ingested_at"(UTC ISO)}`（`ingest.py:89-93`），`app/knowledge_sources/product_faq.py` 写入 `{"ingested_at"}`（L65-66），`app/knowledge_sources/project_docs.py` 写入 `{"doc", "ingested_at"}`（L63-64，`doc` 记来源文件名供 `[n]` 引用溯源）；`ingested_at` 是 `max_age_days` 超龄过滤的判定依据（schema 自由 JSON，DB 零改动） |
| created_at (DateTime tz) | created_at (datetime) | ✓ | `func.now()` | 创建时间 |

索引：
- `ix_knowledge_chunks_source (source)` — 按知识源过滤检索

### knowledge_sources

对应实体：`KnowledgeSourceRow`（`lubanagent/persistence/models/knowledge.py:28`），迁移 `migrations/versions/0006_knowledge_sources.py`（revision `b8c9d0e1f2a3`，down_revision `a7b8c9d0e1f2`）。**只存工作台创建的托管源；代码种子源（project-docs/product-faq）不入表**，列表时与 registry 合并展示（`managed=False`）。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer) | id (int) | ✓ | — | 主键 |
| name (String(128), unique) | name (str) | ✓ | — | 知识源名，即 Agent YAML `knowledge.sources` 引用的 key（`models/knowledge.py:34`） |
| description (Text) | description (str) | ✓ | `""` | 描述 |
| chunk_size (Integer) | chunk_size (int) | ✓ | `500` | 切块参数（rechunk 可改后重灌） |
| overlap (Integer) | overlap (int) | ✓ | `50` | 切块参数 |
| files (JSON) | files (list[dict]) | ✓ | `list` | 落盘文件清单 `[{"filename": str, "size": int}]`（重新切块按目录重读，`models/knowledge.py:39`） |
| chunk_count (Integer) | chunk_count (int) | ✓ | `0` | 当前块数（创建预留行时为 0；发布时回写，重切与旧块替换同事务更新） |
| created_at (DateTime tz) | created_at (datetime) | ✓ | `func.now()`（server_default） | 创建时间 |
| updated_at (DateTime tz) | updated_at (datetime) | ✓ | `func.now()`（server_default + onupdate） | 更新时间 |

索引：主键 `id`；`name` 唯一约束（路由层先查返回 409；manager 仍在灌库前通过唯一预留行认领，同名/遗留数据报 `CONFIG_ERROR`，防止并发请求触碰彼此资源）。`files=[]` 是创建中已提交的预留行，`restore`/列表会跳过，进程崩溃后可能遗留，需人工核查清理（`manager.py:95-103、125、208-234`）。

### session_messages

对应实体：`SessionMessageRow`（`lubanagent/persistence/models/memory.py:11`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer, autoincrement) | id (int) | ✓ | — | 主键 |
| session_id (String(128)) | session_id (str) | ✓ | — | 会话标识 |
| run_id (String(36)) | run_id (str \| None) | ✗ | NULL | 关联产生该消息的 Run |
| role (String(16)) | role (str) | ✓ | — | `user`/`assistant`/`system`/`tool`（注释见 L19） |
| content (Text) | content (str) | ✓ | — | 消息文本 |
| created_at (DateTime tz) | created_at (datetime) | ✓ | `func.now()` | 创建时间 |

索引：
- `ix_session_messages_session_id_id (session_id, id)` — 按会话取最新 N 条窗口查询

"不寄生 runs 表——runs 是执行记录，消息是对话事实"（`lubanagent/persistence/models/memory.py:12`）。

### session_summaries

对应实体：`SessionSummaryRow`（`lubanagent/persistence/models/memory.py:28`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer, autoincrement) | id (int) | ✓ | — | 主键 |
| session_id (String(128), unique) | session_id (str) | ✓ | — | 每会话唯一一行 |
| summary (Text) | summary (str) | ✓ | — | 摘要全文 |
| upto_message_id (Integer) | upto_message_id (int) | ✓ | — | 标记已压缩到的消息 id（消息不物理删除） |
| updated_at (DateTime tz) | updated_at (datetime) | ✓ | `func.now()`，`onupdate=func.now()` | 更新时间 |

索引：无额外索引（`session_id` 唯一约束自带索引）。

### long_term_memories

对应实体：`LongTermMemoryRow`（`lubanagent/persistence/models/memory.py:42`）

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| id (Integer, autoincrement) | id (int) | ✓ | — | 主键 |
| agent_key (String(128), index) | agent_key (str) | ✓ | — | 归属 Agent |
| user_key (String(128), index) | user_key (str) | ✓ | — | 归属用户 |
| fact (Text) | fact (str) | ✓ | — | 事实文本 |
| created_at (DateTime tz) | created_at (datetime) | ✓ | `func.now()` | 创建时间 |

索引：
- `ix_long_term_memories_agent_key (agent_key)`
- `ix_long_term_memories_user_key (user_key)`

（四张表建表来源：`migrations/versions/0003_knowledge_memory_tables.py`）

## 行为

### 状态机

无。

### 调用树

#### 知识摄入（ingest_file）

直接调用 `ingest_file` 或 `ingest_files`：replace=False 先查同源已有块数作为切块剩余额度，解析→切块→向量化后在同源写事务内核对预留所有权、复核已有块数并追加；replace=True 仅按新块数限额，在同一事务内核对预留所有权、删除旧块并写新块。待发布托管行（`files=[]`）只有向 `ingest_files`/`store.write_chunks` 传入匹配 `reservation_id` 才可写；普通写入口及 `store.delete_source` 均拒绝。返回本次写入块数（非追加后的总块数）；异常回滚本次 DB 写入。已发布托管行及无托管行的种子写行为不变，用户领地种子源也可自行调用 `chunk_text`/`KnowledgeStore` 底层方法（`ingest.py:360-418`、`store.py:84-151`）。

```
ingest_file(path, *, source, store, embedder, replace=True)  # ingest.py:407-418，转调 ingest_files
└── ingest_files(paths, *, source, store, embedder, replace, max_chunks=5000, chunk_params=None, reservation_id=None)  # ingest.py:360-404
    ├── [replace=False] store.count_chunks(source)  # 预检查现存块；已超配额先拒绝
    ├── 对每个 path：parse_document(path) → chunk_text(text, max_chunks=max_chunks-existing-已生成块数)
    │   └── PDF/DOCX：父进程创建私有目录 → 跟踪并 shield 启动子进程 → 子进程有界复制源文件到快照并解析；启动/复制/解析共用 deadline，取消/超时回收进程和目录（ingest.py:147-200、223-309）
    ├── embedder.embed(chunks)  # 非空时一次向量化；metadata 含 filename + ingested_at
    └── store.write_chunks(source, records, replace=replace, max_chunks=max_chunks, chunk_params=chunk_params, reservation_id=reservation_id)
        └── source_transaction(source) → _check_reservation 核对待发布行所有权 → 重查现存块 → 校验新块 +（追加时）现存块 ≤ max_chunks
            → [replace=True] 删旧块 → 写新块 → [chunk_params 非空] 更新托管行参数及 chunk_count → 提交
```

真实案例（`app/knowledge_sources/product_faq.py::ingest_product_faq`，L49-70）不复用 `ingest_file`，而是自行拼装文本后调 `chunk_text`/`embedder.embed`/`store.add_chunks`：`store.delete_source(_SOURCE)` → `_load_products()`（优先 HTTP 拉取 Mock 业务服务 `GET {biz_base_url}/products/search`，失败降级读本地 `product_faq_seed.json`，L32-45）→ 拼 `"{name}：{faq}"` 文本 → `chunk_text(chunk_size=200, overlap=20)` → `embedder.embed` → `store.add_chunks`（metadata 同样打 `ingested_at` UTC ISO 时间戳，L65-66）。

#### 托管知识源创建（POST /v1/knowledge/sources → create_source）

工作台上传多文件创建托管知识源的调用树（`lubanagent/knowledge/manager.py:181-308`）：

```
create_source(*, name, description, chunk_size, overlap, files)  # manager.py:181-308
├── _validate_name/_validate_chunk_params/_validate_files  # 名称、参数、后缀、10 MiB/50 MiB、basename
├── 独立 reserve 任务：store.source_transaction(name)  # 同源写事务内拒绝已有行/目录/块/registry
│   └── INSERT knowledge_sources(name, files=[], chunk_count=0) → shield 等待任务完成提交后取得 owned_id；调用方取消时仍等待任务，只有成功提交才取得 id（灌库前认领唯一名）
├── files_root 下建同根暂存目录、逐文件写入 → ingest_files(..., replace=True, reservation_id=owned_id)
│   └── 逐文件 parse_document → 最多 5000 块 → embed → store.write_chunks 在同源事务内核对待发布行 id 后写块
├── 独占 mkdir 正式目录 → 从暂存目录移入文件
├── store.source_transaction(name) 按 owned_id 回写 files/chunk_count 并注册 connector
└── 失败/取消时仅在已取得 owned_id 且该行 files 仍为空时按 source 清理块及本次行/目录、撤销本次注册；已提交发布不回滚（返回前取消需查询确认）；finally 清理本次暂存目录
```

配套行为：
- **恢复注册（restore 语义）**：Registry 是 Agent YAML 知识源的运行期解析路径且进程内易失，`lubanagent/app/factory.py` 的 lifespan 在用户 `on_startup` 钩子前 `await knowledge_manager.ensure_restored()`（失败仅告警不阻断启动）——只对 `files` 非空的托管行注册 connector，已注册名跳过；空 `files` 预留行跳过而非自动恢复/清理；`list_sources`/`get_source` 还会懒调 `ensure_restored()`（`manager.py:88-161`）。
- **重新切块（rechunk）**：`manager.rechunk` 从落盘目录重读白名单后缀文件，调用 `ingest_files(..., replace=True, chunk_params=(chunk_size, overlap))`；解析/向量化/配额检查失败时旧块和旧参数不变，写事务中任一步失败则旧块删除、新块写入与行的参数/块数更新一起回滚（仅 DB 内原子性，`manager.py:312-346`、`store.py:84-117`）。上传后换 embedding 模型导致旧向量失配时，重新切块即重 embed。
- **检索测试（POST /v1/knowledge/query → manager.search）**：`embedder.embed([query])` → `store.search(query_embedding, source, top_k)`（source=None 全源）；不经 Run、不过 `capabilities` 守门（`score_threshold`/`max_age_days` 不生效），是管理工作台里的裸检索视图（`manager.py:166-177`）。

#### 知识检索（Run 内经 capabilities.prepare 触发）

由 `lubanagent/runtime/capabilities.py::prepare` 在装配 Agent 上下文时，对 `definition.knowledge.sources` 中每个已注册知识源发起检索；结果拼装进 system prompt 尾部供模型引用。

```
prepare(definition, user_input, ...)  # lubanagent/runtime/capabilities.py:82
└── [use_knowledge] definition.knowledge.sources 非空
    └── for name in sources:
        ├── [name 未注册] registry.list_knowledge() 不含 name
        │   └── raise LubanError(CONFIG_ERROR)  # 显式失败，不静默降级
        └── connector = registry.get_knowledge(name)
            └── connector.retrieve(user_input, top_k=definition.knowledge.top_k)  # KnowledgeConnector.retrieve
                └── SqlKnowledgeConnector.retrieve(query, top_k)  # lubanagent/knowledge/retrieve.py:18
                    ├── embedder.embed([query])  # 取 query 向量
                    └── store.search(query_embedding, source=self._source, top_k=top_k)
                        └── 全量读该 source 切块 → Python cosine 排序 → 截取 top_k
    └── _filter_snippets(snippets, definition.knowledge)  # 防错答守门（capabilities.py:40-66）
        ├── [score_threshold > 0] 丢弃 cosine < score_threshold 的 snippet
        └── [max_age_days 非 None] 丢弃 metadata.ingested_at 超龄 snippet（无时间戳/解析失败保留，兼容存量）
    └── [过滤后 snippets 非空]
        ├── [cite=True] _KNOWLEDGE_HEADER_CITE + format_snippets_for_prompt(snippets)
        │   # 强引用约束头（"只允许基于以下编号片段作答……无法从片段得出时必须直接回答不知道……引用来源时使用 [n] 编号"）+ "[1] ...\n[2] ..." 编号（capabilities.py:31-35）
        └── [cite=False] _KNOWLEDGE_HEADER_PLAIN + 纯片段罗列（无编号无约束句，capabilities.py:36、162-163）
    └── [过滤后 snippets 为空 且 no_hit_fallback 非 None]
        └── CapabilityContext.knowledge_no_hit = True  # 且不注入知识 suffix；agent_loop 据此短路拒答（见 runtime 模块文档）
```

#### 记忆策略：会话记忆读写

```
prepare(...)  # 上下文组装前
└── [use_history] memory.session 或 memory.summary 且 session_id 非 None
    └── [memory.summary=False 或未配 summarizer] SessionMemory.history(session_id, window_size)
        └── 取最新 window_size 条，按时间升序返回 → 作为 request.history 覆盖值

finalize(...)  # Run 收尾后（lubanagent/runtime/capabilities.py:142）
└── [memory.session 或 memory.summary] 且 session_id 非 None
    ├── SessionMemory.append(session_id, "user", user_input, run_id)
    └── [output is not None] SessionMemory.append(session_id, "assistant", output, run_id)
    # 异常只记日志不阻断：Run 已终态，记忆是附属（L155 docstring）
```

#### 记忆策略：摘要记忆读写

```
prepare(...)
└── [memory.summary=True 且 summarizer 已配置] SummaryMemory.context_prefix(session_id, window_size)
    ├── [有既有 summary_row] SystemMessage("此前对话摘要：...") + upto_message_id 之后窗口内消息
    └── [无] 退化为 SessionMemory.history(session_id, window_size)
    # summary=True 但 summarizer=None → 记 warning，退化为 SessionMemory.history（不炸）

finalize(...)
└── [memory.summary=True]
    └── SummaryMemory.maybe_compress(session_id, window_size)  # lubanagent/memory/summary.py:72
        ├── 统计未压缩消息数（id > 既有 upto_message_id）
        ├── [总数 <= window_size] 不触发，返回 False
        └── [总数 > window_size]
            ├── 取窗口外最旧 overflow 条消息
            ├── summarizer.summarize(messages, previous=旧摘要)  # 库事务外调用，可能调 LLM
            └── upsert SessionSummaryRow（更新 summary + upto_message_id，或新建）
```

#### 记忆策略：长期记忆读写

```
prepare(...)
└── [use_long_term] memory.long_term=True 且 user_key 非空
    └── LongTermMemory.recall(definition.key, user_key)  # 最近 limit=20 条，正序
        └── format_facts_for_prompt(facts) → 非空则拼入 suffix（"已知用户信息：..."）

# 写入是显式调用（非 Run 生命周期自动触发）：
LongTermMemory.remember(agent_key, user_key, fact)  # 由业务代码/工具主动调用写入事实
LongTermMemory.forget(agent_key, user_key, fact_id)  # 限定 agent+user 删除，防越权
```

## 边界规则

- `KnowledgeConfig.top_k` 默认 5、`cite` 默认 `True`、守门三字段 `score_threshold` 默认 `0.0`（不过滤）、`no_hit_fallback` 默认 `None`（不短路）、`max_age_days` 默认 `None`（不过滤）；`MemoryConfig.session` 默认 `True`、`window_size` 默认 20、`summary`/`long_term` 默认 `False`（来源：`lubanagent/core/definition.py:35-49`）。四项能力均可独立开关，未启用时 `prepare` 对应分支零读写、`finalize` 直接 early-return（来源：`lubanagent/runtime/capabilities.py:106-112`、`:211-213`）。守门三字段全默认时检索行为与旧版完全一致（零行为变化，存量兼容）。
- Agent 定义引用的知识源名若未在 `Registry` 注册（`registry.list_knowledge()` 不含该名），`prepare` 显式抛 `LubanError(CONFIG_ERROR)` 导致 Run 失败，不做静默降级（来源：`lubanagent/runtime/capabilities.py:102-107`；验收用例 `tests/lubanagent/runtime/test_capabilities.py::test_missing_knowledge_source_fails_run`）。
- `Registry.register_knowledge` 对同名知识源重复注册抛 `LubanError(CONFIG_ERROR, "知识源重复注册: {name}")`；`get_knowledge` 对未注册名抛 `LubanError(VALIDATION_ERROR, "知识源未注册: {name}")`；新增 `unregister_knowledge(name)` 幂等注销（未注册名静默跳过）（来源：`lubanagent/extension/registry.py:103-120`）。
- **托管源与代码种子源双轨**：`knowledge_sources` 表只存工作台创建的托管源（managed，含创建中 `files=[]` 的预留行）；代码种子源（project-docs/product-faq）保持代码驱动不入表、不迁移——种子源每次启动整源重灌，与用户编辑会打架，读时合并（`list_sources` 返回 files 非空的 DB 行 ∪ registry-only 名）保每个来源单一 source of truth（来源：`lubanagent/knowledge/manager.py:1-5、113-138`、`lubanagent/persistence/models/knowledge.py:28-29`）。非托管源不可删、不可重切（`_get_managed` 拦截 → `VALIDATION_ERROR`，`manager.py:350-364`）。
- **api → knowledge 分层禁忌**：`lubanagent/api/routes/knowledge.py` 禁止 `import lubanagent.knowledge`（import-linter 约束，同 skills 先例），只能经 `request.app.state.knowledge_manager` 鸭子类型取用（manager 由 `lubanagent/app/factory.py` 构造挂载）；`knowledge_manager` 未装配时路由抛 `CONFIG_ERROR`（防御性，来源：`lubanagent/api/routes/knowledge.py:1-6、31-36`）。
- **上传及暂存边界**：路由在读取前拒绝超过 50 个文件，逐文件按 64 KiB 读取，单文件超过 10 MiB、请求累计超过 50 MiB 时立即拒绝且不读取后续文件/不调用 manager；合格数据仍以 bytes 列表暂存在进程内。manager 再验证大小、后缀与 basename，源目录 resolve 后须位于 files_root 内（`api/routes/knowledge.py:25-29、72-110`；`knowledge/manager.py:30-31、366-375、395-422`）。`create_source` 在灌库前以 DB 唯一预留行认领名字并拒绝已有目录/孤立旧块；预留事务由被 `asyncio.shield` 保护的独立任务完成提交，调用方取消时等待取得实际已提交的 `owned_id`，并将 id 透传给 `ingest_files`/`store.write_chunks`。`KnowledgeStore` 的 `write_chunks`/`add_chunks`/`delete_source` 在同源事务内检查 `knowledge_sources.files=[]`：无 id 的正常写/删块入口拒绝该源（`CONFIG_ERROR`），`write_chunks` 仅在 id 匹配且行仍待发布时允许创建者写入；已有已发布托管行及无托管行的种子写行为不变。故取消清理按 source 删块仅在仍拥有该待发布行、且其他经 store 的正常写入口不能同名写时执行；不保证绕过 store 的直接 SQL 写入受保护。预留失败或竞争者未取得 id 时不清理他人资源；最终提交发布后到返回前发生取消不会回滚已发布数据，调用者可查询确认。进程崩溃可能遗留空 `files` 预留行及暂存目录，需人工核查；DB 与文件目录不是同一事务，注册恢复跳过空行且不自动清理；MySQL 跨实例路径未集成验证（`manager.py:208-308`、`store.py:84-151`）。
- **落盘根目录**：托管源文件落 `settings.knowledge_files_root`（默认 `data/knowledge`，env `KNOWLEDGE_FILES_ROOT`）下每源一目录；新建在灌库前拒绝同名源或遗留目录/块并预留唯一行，不覆盖旧数据；删除托管源删整个目录；重新切块从盘上重读（来源：`lubanagent/config/settings.py:51`、`lubanagent/knowledge/manager.py:201-219、300-346`）。
- **FakeEmbedding 阈值标定 caveat**：`score_threshold` 守门阈值是对特定 embedder + 特定语料实测标定的（project-docs 实测分界 0.24/0.235）；经工作台动态创建的托管源若配进 Agent YAML `knowledge.sources`，其语料分布不同，需先用 `POST /v1/knowledge/query` 检索测试自测正例/无关查询的 score 分布再定 `score_threshold`，不能照抄范本数值（来源：`lubanagent/knowledge/embedding.py:61-69` docstring 的"不承诺生产检索质量"边界 + `app/agents/dept_project.yaml` 的标定注释）。上传后换 embedding 模型 → 旧向量失配，重新切块即重 embed（`manager.rechunk`）。
- `FakeEmbedding` 零密钥兜底：不是运行期自动探测触发，而是由装配方显式选用——embedder 选择逻辑在**框架装配根** `lubanagent/app/factory.py`（按 `settings.embedding_model` 是否非空二选一：非空 → `OpenAICompatibleEmbedding`，空 → `FakeEmbedding`，`factory.py:82-90`；knowledge 模块不碰 config，`knowledge → config` 亦是 import-linter 禁忌），用户领地 `app/main.py` 复用 `app.state.knowledge_store/knowledge_embedder` 同一实例（`app/main.py:62-65`）；其向量是"确定性 token 集合 one-hot hash"（`_STOPWORDS` 高频功能字滤除），docstring 明确"只服务测试与零密钥演示，不承诺生产检索质量"（来源：`lubanagent/knowledge/embedding.py:61-69`）。若改用 `OpenAICompatibleEmbedding` 且配置的 `api_key_env` 环境变量未设置，则抛 `LubanError(CONFIG_ERROR)`（来源：`lubanagent/knowledge/embedding.py:36-43`）；未安装 `langchain_openai` 同样抛 `CONFIG_ERROR`（L44-51）。
- **防错答守门（代码强制，不靠 Prompt 声明）**：检索结果先经 `_filter_snippets` 过滤（`lubanagent/runtime/capabilities.py:40-66`）——`score_threshold > 0` 时 cosine 低于阈值的 snippet 丢弃；`max_age_days` 非 None 时 `metadata.ingested_at` 早于 `now(UTC) - max_age_days` 的 chunk 丢弃，**无时间戳或解析失败的 chunk 保留**（兼容存量数据，不因脏数据误杀）。过滤后 snippets 为空且 `no_hit_fallback` 非 None 时置 `CapabilityContext.knowledge_no_hit = True` 且不注入知识 suffix；`agent_loop` 据此短路拒答——不调模型（零 token、无 MODEL_* 事件），`output = no_hit_fallback`，Run 以 `(COMPLETED, EndReason.KNOWLEDGE_NO_HIT)` 终态（`lubanagent/runtime/agent_loop.py:410-425`；短路细节见 runtime 模块文档）。
- `FakeEmbedding` 默认维度逐级实测提升到 2048，并新增高频功能字过滤 `_STOPWORDS`（`lubanagent/knowledge/embedding.py:14-16、72`）：64 维 hash 桶碰撞使无关查询 cosine 虚高到 0.3+；512 维在小语料够用，但在文档级语料（数百 chunk × 百级 token，实测 484 chunks）下桶饱和，无关查询 cosine 仍虚高；2048 维 + 功能字过滤后实测正例查询 0.24-0.42、无关查询 0.13-0.235（上界 < 阈值 0.24），`score_threshold` 守门可校准。确定性语义不变（同文本同向量）。**存量 DB 中的旧维度向量与新向量维度不齐，需重新灌入**——`app` 启动 `on_startup` 钩子自动 replace 重灌（`app/main.py:46-53`）。
- `KnowledgeStore.search` 的 `top_k` 钳制到 `[1, MAX_SEARCH_TOP_K=100]`（`store.py:41`）：下限 1 防 0/负数被切片静默吞掉全部结果，上限 100 是响应规模护栏；不抛错、不吞结果（`store.py:165-198`）。存量调用方边界：API 层 `api/schemas.py` 已限 1..50、`KnowledgeConfig.top_k` 默认 5，钳制是框架层兜底（`store.py:38-40` 注释）。
- `chunk_text` 强制 `chunk_size > 0` 且 `0 <= overlap < chunk_size`，否则抛 `LubanError(VALIDATION_ERROR)`（`lubanagent/knowledge/ingest.py:329-339`）。
- **解析及灌库配额**：`parse_document` 默认单文件输入 ≤10 MiB、输出文本 ≤2,000,000 字符，PDF ≤200 页；DOCX ZIP ≤512 成员、单成员 ≤10 MiB、总实际展开 ≤50 MiB，第三方 `Document` 调用前对全容器（包括图片等非正文成员）先查目录头再按成员实际读取核验。PDF/DOCX 的私有快照目录由父进程创建并在返回、超时或取消后回收；`_worker_main` 在子进程内先调用 `_copy_limited_snapshot` 再解析快照，复制与解析不再由父进程的线程承载。`_parse_in_child` 对启动、复制、解析共用单个 `parse_timeout` deadline（默认 30 秒），超时转 `RUN_TIMEOUT`；启动由独立 shield 任务跟踪，启动中取消/超时仍等待取得句柄、杀直接子进程并 wait；启动后取消/超时/异常路径杀直接子进程、等待进程与两条管道任务回收。Linux 子进程另设 CPU 和 512 MiB 地址空间 rlimit；macOS 不声明 RLIMIT_AS 有效，也没有容器级隔离或跨进程异常完全隔离保证（`ingest.py:172-200、211-309`；`tests/lubanagent/knowledge/test_ingest.py:623-660`）。`_copy_limited_snapshot` 对普通源文件使用不可跟随链接的 fd，以 64 KiB 块复制到私有快照，默认最多读 10 MiB+1 字节判超额；解析仅读快照，复制期间源文件被替换也不能绕过实际字节配额。普通文本等非 PDF/DOCX 路径仍用 `asyncio.to_thread` 读取/解析，取消协程不等于可终止该线程（`ingest.py:147-169、223-240、312-340`；`core/zip_quota.py:36-88`）。`ingest_files` 默认最多 5000 块：replace=True 只计本次新块，replace=False 先计已有块并把余额传给 `chunk_text`，写事务内再次统计已存块校验追加总量；解析/块数超额或事务写失败不会留下部分替换（`ingest.py:360-402`、`store.py:84-117`）。MySQL `GET_LOCK` 分支有实现但未做集成实测，不宣称跨实例锁行为已验证（`store.py:50-82`）。
- `parse_document` 支持 `SUPPORTED_SUFFIXES = {.md .txt .pdf .docx .csv .json .html .xml}` 八种后缀（`.htm` 也能解析但不在白名单集合），其余抛 `LubanError(VALIDATION_ERROR)` 且 details 带 supported 全集；`.pdf` 依赖可选包 `pypdf`、`.docx` 依赖可选包 `python-docx`（均属 pyproject optional extra `docs = ["pypdf>=4.0", "python-docx>=1.1"]`，也进 dev 依赖），未安装时报错并附带 pip 安装提示（`lubanagent/knowledge/ingest.py:93-120、123-129、252-274`）。
- 摘要记忆触发条件：`SummaryMemory.maybe_compress` 在"未压缩消息数（id > 既有 upto_message_id）> window_size"时才触发一次压缩，已压缩消息（`id <= upto_message_id`）不删除、不重复计数（来源：`lubanagent/memory/summary.py:60`、`:72-92`）。摘要调用 LLM 发生在 DB 事务之外（来源：`lubanagent/memory/summary.py:112` 注释）。
- `memory.summary=True` 但未提供 `summarizer` 实例：`prepare` 记 warning 退化为纯 `SessionMemory.history`；`finalize` 同样记 warning 并跳过压缩，不抛异常（来源：`lubanagent/runtime/capabilities.py:81-85`、`:166-169`；验收用例 `test_summary_without_summarizer_degrades_gracefully`）。
- `finalize` 中记忆回写（`SessionMemory.append`/`SummaryMemory.maybe_compress`）异常被捕获仅记日志，不影响 Run 终态（"Run 已终态，记忆是附属"）（来源：`lubanagent/runtime/capabilities.py:174-175`；验收用例 `test_finalize_failure_does_not_block_run`）。
- `LongTermMemory.recall` 默认 `limit=20`，按 `created_at desc, id desc` 取最近 N 条后反转为正序返回；`forget` 删除时限定 `agent_key`+`user_key` 双重匹配，防止跨 Agent/跨用户越权删除（来源：`lubanagent/memory/long_term.py:66-88`、`:90-100`）。容量上限（风险 R10 修复）：构造参数 `capacity`（默认 `Settings.long_term_memory_capacity=1000`，env `LONG_TERM_MEMORY_CAPACITY`），`remember` 超限即淘汰同 agent+user 维度最旧条目（来源：`long_term.py:14-22、34-64`）；无 TTL/过期机制，`long_term_memories` 表无过期字段，保留至显式 `forget` 或容量淘汰。
- 长期记忆的注入前提是 `use_long_term = memory_cfg.long_term and bool(user_key)`——`long_term=True` 但调用方未提供 `user_key`（如 `runtime.run` 未配置 `user_key_fn`）则跳过注入，不报错（来源：`lubanagent/runtime/capabilities.py:67`、`:92`；验收用例 `test_long_term_memory_injected_with_user_key`）。
- system prompt 尾部拼接顺序固定：长期记忆 → 知识片段 → Skill 元数据（`suffix_parts` 依次 append，来源：`lubanagent/runtime/capabilities.py:131-186`），且遵循"动态后置、稳定前缀不变"的 KV-cache 语义（来源：同文件 L5-6 模块 docstring）。
- `SessionMessageRow.role` 只接受 `user`/`assistant`/`system`/`tool` 四种，`row_to_message` 遇未知 role 抛 `LubanError(INTERNAL_ERROR)`；`tool` 角色回放时 `tool_call_id` 以空串占位，标注"回放待 Run Trace 重建"（来源：`lubanagent/memory/session.py:17-31`）。
- `app/knowledge_sources/product_faq.py` 的知识源灌入失败不阻断应用启动（零配置兜底），但会显式记录告警，不静默吞异常（来源：`app/main.py:46-53`，注释 L52；project-docs 知识源同受此兜底）；降级种子文件 `product_faq_seed.json` 固定放在用户领地内（`app/knowledge_sources/`），不跨领地读 `demos/` 目录（来源：`app/knowledge_sources/product_faq.py:21`）。

## 名词

| 术语 | 语义 |
|---|---|
| KnowledgeConnector | Protocol，定义 `async retrieve(query, top_k) -> list[Snippet]`，是知识源的统一扩展点；唯一定义在 `lubanagent/extension/protocols.py`，`lubanagent/knowledge/connector.py` 仅 re-export（决策 D0-2，来源：`lubanagent/knowledge/connector.py:1`） |
| Snippet | Protocol 返回的知识检索片段结构：`content`/`source`/`score`/`metadata` 四字段（来源：`lubanagent/extension/protocols.py:78-84`） |
| EmbeddingProvider | Protocol，定义 `async embed(texts) -> list[list[float]]`，Embedding 能力的统一抽象，两个内置实现为 `OpenAICompatibleEmbedding` 与 `FakeEmbedding`（来源：`lubanagent/knowledge/embedding.py:19-21`） |
| FakeEmbedding | 零密钥场景下的确定性 hash 向量兜底实现，同文本产出同向量，关键词重叠越多 cosine 越高；默认 dim=2048 且滤除 `_STOPWORDS` 高频功能字（两个去噪设计让守门阈值在文档级语料下可校准）；仅供测试/演示，不承诺生产检索质量（来源：`lubanagent/knowledge/embedding.py:61-85`） |
| KnowledgeStore | knowledge_chunks 表的存取封装类；`write_chunks` 在同源事务内核对待发布预留行 id 并校验配额后替换/追加，可同步更新托管行重切参数；`add_chunks`/`delete_source` 拒绝待发布源；`search` 用全量读 + Python cosine 排序（向量存 DB JSON，非专用向量库；来源：`lubanagent/knowledge/store.py:44-214`） |
| KnowledgeSourceManager | 工作台托管知识源全生命周期管理类（创建落盘/切块入库/注册/重切/删除/检索测试/重启恢复注册），由 `lubanagent/app/factory.py` 装配挂 `app.state.knowledge_manager`，路由层经鸭子类型访问（来源：`lubanagent/knowledge/manager.py:67`） |
| 托管源（managed）/ 内置源（registry-only） | 两种知识源形态：托管源 = 经 `POST /v1/knowledge/sources` 创建、元数据落 `knowledge_sources` 表、文件落盘可重切可删；内置源 = 代码种子（project-docs/product-faq），只在 Registry 注册、参数不可改不可删，列表时以 `managed=False` 合并展示（来源：`lubanagent/knowledge/manager.py:116-141`） |
| SqlKnowledgeConnector | `KnowledgeConnector` 的框架内置实现，组合 `KnowledgeStore` + `EmbeddingProvider` 完成 embed→search→组装 Snippet（来源：`lubanagent/knowledge/retrieve.py:8`） |
| 会话记忆（SessionMemory） | 基于 `session_messages` 表的滑动窗口式历史消息读写，跨轮保上下文（来源：`lubanagent/memory/session.py:34-35`） |
| 摘要记忆（SummaryMemory） | 消息数超窗口时把窗口外最旧消息压缩进 `session_summaries` 表的策略，摘要在前缀、窗口内消息 append-only（来源：`lubanagent/memory/summary.py:1`） |
| 长期记忆（LongTermMemory） | 以 `agent_key + user_key` 维度存储的跨会话关键事实，与具体 `session_id` 无关、需显式 `remember` 写入；每维度条数有容量上限（默认 1000，`LONG_TERM_MEMORY_CAPACITY` 可调），超限淘汰最旧（来源：`lubanagent/memory/long_term.py:1、12-15`） |
| Summarizer | Protocol，定义 `async summarize(messages, *, previous) -> str`；内置 `FakeSummarizer`（确定性拼接截断，测试用）与 `LlmSummarizer`（经 brain cheap 槽位模型）两个实现（来源：`lubanagent/memory/summary.py:23-56`） |
| CapabilityContext | `capabilities.prepare` 的产出 dataclass：`system_suffix`/`extra_tools`/`history`/`knowledge_no_hit`（+ 守门观测 `knowledge_hits_raw`/`knowledge_hits_kept` 过滤前后条数，随 KNOWLEDGE_NO_HIT 事件外发）字段，供 `agent_loop` 注入上下文与工具增量、触发知识全 miss 短路拒答（来源：`lubanagent/runtime/capabilities.py:69-83`） |
| 防错答守门（score_threshold / no_hit_fallback / max_age_days） | KnowledgeConfig 三个可选字段构成的"可不答、不硬编"防线：阈值过滤弱相关片段、时效过滤超龄 chunk、全 miss 短路拒答；全部带默认值，未配置时零行为变化（来源：`lubanagent/core/definition.py:39-42`、`lubanagent/runtime/capabilities.py:40-66`） |
| knowledge_no_hit | 知识检索经守门过滤后全 miss 且配置了 `no_hit_fallback` 时 `CapabilityContext` 置位的标志；`agent_loop` 据此跳过模型执行直接以兜底话术作答，emit `KNOWLEDGE_NO_HIT` 事件（来源：`lubanagent/runtime/capabilities.py:77-79`、`agent_loop.py:410-425`） |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent/persistence`（`Database`） | `db.session()` | knowledge 通过 `KnowledgeStore` 读写 `knowledge_chunks` 表、`KnowledgeSourceManager` 读写 `knowledge_sources` 表；memory 通过 `SessionMemory`/`SummaryMemory`/`LongTermMemory` 读写 `session_messages`/`session_summaries`/`long_term_memories` 三表（来源：`lubanagent/knowledge/store.py:8`、`lubanagent/knowledge/manager.py:25`、`lubanagent/memory/session.py:13-14`、`lubanagent/memory/summary.py:9-10`、`lubanagent/memory/long_term.py:5-6`） |
| `lubanagent/app/factory.py`（框架装配根） | 构造 `KnowledgeStore`/embedder/`KnowledgeSourceManager` 挂 `app.state.knowledge_*` | embedder 选型（`settings.embedding_model` 非空 → `OpenAICompatibleEmbedding`，空 → `FakeEmbedding`）与 manager 装配都在工厂（knowledge 模块不碰 config）；lifespan 启动时 `ensure_restored()` 恢复托管源注册（来源：`lubanagent/app/factory.py:80-98、101-107、126-129`） |
| `app/knowledge_sources/product_faq.py`（用户领地） | `make_product_faq_connector` / `ingest_product_faq` | 实现 `KnowledgeConnector` 的真实知识源（product-faq），装配 `SqlKnowledgeConnector` + embedder + `KnowledgeStore`，向 `default_registry` 注册知识源；store/embedder 复用工厂装配的 `app.state.knowledge_*` 同一实例（选型逻辑在 factory，`app/main.py:62-67`）；数据来源优先 HTTP 拉取 Mock 业务服务，失败降级本地 `product_faq_seed.json`（来源：`app/knowledge_sources/product_faq.py:21-45`） |
| `app/knowledge_sources/project_docs.py`（用户领地） | `make_project_docs_connector` / `ingest_project_docs` | 部门项目问答范本知识源（source=project-docs）：五份核心文档清单固定在 `_DOC_FILES`（PRD/用户手册/技术方案/架构设计/AGENTS.md），逐文档 read_text → `chunk_text(chunk_size=300, overlap=30)` → embed → 整源 replace 入库；metadata 记 `doc`（来源文件名，`[n]` 引用溯源）与 `ingested_at`；`make_project_docs_connector` 注册到 `default_registry`（来源：`app/knowledge_sources/project_docs.py:21-33、41-79`） |
| `lubanagent/extension`（`Registry`/`default_registry`） | `register_knowledge` / `get_knowledge` / `list_knowledge` | knowledge 检索前需先在 `Registry` 完成注册，`capabilities.prepare` 按名字查找（来源：`lubanagent/extension/registry.py:102-115`） |
| `langchain_openai`（可选依赖） | `OpenAIEmbeddings.aembed_documents` | `OpenAICompatibleEmbedding` 懒加载调用，未安装则 `CONFIG_ERROR`（来源：`lubanagent/knowledge/embedding.py:39-52`） |
| `pypdf`（可选依赖） | `PdfReader` | `parse_document` 解析 `.pdf` 时懒加载，未安装则 `VALIDATION_ERROR`（来源：`lubanagent/knowledge/ingest.py:20-27`） |
| `langchain_core.language_models.chat_models.BaseChatModel` | `model.ainvoke` | `LlmSummarizer` 经调用方注入的 brain cheap 槽位模型完成摘要生成（来源：`lubanagent/memory/summary.py:45-56`） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent/runtime`（`capabilities.prepare`/`finalize`，属 runtime 模块） | `prepare(definition, user_input, session_id, user_key, db, registry, ...)` / `finalize(definition, session_id, run_id, user_input, output, db, ...)` | Run 上下文组装前调用 knowledge 检索（含守门过滤与 no_hit 标记）+ 三种记忆读取拼装 `system_suffix`/`history`；Run 收尾后调用会话/摘要记忆写入（来源：`lubanagent/runtime/capabilities.py:82-193、196-229`） |
| Agent system prompt / 模型输入 | 经 `CapabilityContext.system_suffix`、`.history` | 知识片段（含 `[n]` 引用编号说明）、长期记忆事实、摘要前缀等注入模型可见上下文，直接影响模型回答内容（来源：`lubanagent/runtime/capabilities.py:29-30、90-133`） |
| `lubanagent/api/routes/knowledge.py` | `app.state.knowledge_manager`（鸭子类型，不 import knowledge） | 工作台知识库 6 个管理端点（列表/上传/删除/重切/切块查看/检索测试）全部委托 `KnowledgeSourceManager`（来源：`lubanagent/api/routes/knowledge.py:53-133`） |
