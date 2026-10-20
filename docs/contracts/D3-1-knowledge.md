# 契约 D3-1：`lubanagent/knowledge` 最小 RAG

> 来源：开发计划 D3-1、技术方案 §5.6、PRD F9（运行期可选能力，默认不启用）。
> 前置：D0-2（KnowledgeConnector/Snippet 已在 extension/protocols.py 定义）、D1-1（仓储/迁移机制）。
> **范围边界**：本单元只做 knowledge 包本体（不接 Runtime）；Runtime 接线（检索注入上下文）由主 agent 统一集成（D3-int），契约见 D3-int 集成契约。

## 范围

- `lubanagent/knowledge/`：`connector.py`、`embedding.py`、`ingest.py`、`retrieve.py`、`store.py`、`__init__.py`
- `lubanagent/persistence/models/`：**追加 `knowledge_chunks` 表（迁移 0003）**
- `tests/lubanagent/knowledge/`

## 契约决策

1. **向量库可选、默认零依赖**：v1 向量存 `knowledge_chunks.embedding`（JSON float 数组），检索 = Python 内 cosine（小规模够用）；`VectorStore` Protocol 预留，外部向量库实现后续可插（TODO）。
2. **Embedding 经独立 Protocol**：`EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`；实现两个：`OpenAICompatibleEmbedding`（langchain_openai OpenAIEmbeddings 懒加载，配置来自显式参数）、`FakeEmbedding`（确定性 hash 向量，测试/零密钥用——同文本同向量、关键词重叠文本相似度更高：用 token 集合 one-hot × 位置无关 hash，保证"产品 FAQ 灌入后相关提问可命中"的测试可行）。
3. **PDF 解析可选依赖**：`pypdf` 未安装时解析 .pdf → LubanError(VALIDATION_ERROR, details={"hint": "pip install pypdf"})；md/txt 直接读。
4. **切块**：按字符滑窗（chunk_size=500、overlap=50），遇段落边界优先断；每块记 source/chunk_index。

## 数据契约（迁移 0003_knowledge_chunks）

`knowledge_chunks`：`id` int pk、`source` str(128)（知识库名）、`chunk_index` int、`content` text、`embedding` JSON（float 数组）、`metadata` JSON（文件名/标题等）、`created_at` server_default now。index(`source`)。

## 接口契约

### connector.py
```python
from lubanagent.extension.protocols import KnowledgeConnector, Snippet  # re-export（唯一定义在 extension，D0-2 决策）
```

### embedding.py
```python
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
class OpenAICompatibleEmbedding:
    def __init__(self, *, model: str, base_url: str | None = None, api_key_env: str | None = None) -> None
        # 懒加载 OpenAIEmbeddings；api_key_env 指向变量缺失 → CONFIG_ERROR
class FakeEmbedding:
    def __init__(self, *, dim: int = 64) -> None   # 决策 2 的确定性向量
```

### store.py
```python
class KnowledgeStore:
    def __init__(self, db: Database) -> None
    async def add_chunks(self, source: str, chunks: list[dict]) -> int
        # chunks: [{"chunk_index", "content", "embedding", "metadata"}]；返回条数
    async def delete_source(self, source: str) -> int
    async def list_chunks(self, source: str | None = None) -> list[dict]
    async def search(self, embedding: list[float], *, source: str | None = None,
                     top_k: int = 5) -> list[dict]
        # 全量读 + Python cosine 排序（决策 1），返回带 "score" 的 chunk dict
```

### ingest.py
```python
async def parse_document(path: Path) -> str            # .md/.txt 直读；.pdf 经 pypdf（决策 3）；未知后缀 → VALIDATION_ERROR
def chunk_text(text: str, *, chunk_size: int = 500, overlap: int = 50) -> list[str]
async def ingest_file(path: Path, *, source: str, store: KnowledgeStore,
                      embedder: EmbeddingProvider, replace: bool = True) -> int
    # parse → chunk → embed → （replace 则先 delete_source）→ add_chunks；返回块数
```

### retrieve.py
```python
class SqlKnowledgeConnector:        # 实现 KnowledgeConnector
    def __init__(self, source: str, *, store: KnowledgeStore, embedder: EmbeddingProvider) -> None
    async def retrieve(self, query: str, top_k: int) -> list[Snippet]
        # embed(query) → store.search → Snippet(content, source, score, metadata=含 chunk_index)
def format_snippets_for_prompt(snippets: list[Snippet]) -> str
    # "[1] content...\n[2] ..." 编号格式（引用规则：模型回答带 [n] 引用，D3-int 接线时注入 system prompt 尾部——动态后置）
```

### __init__.py
re-export 上述全部公开名。

## 行为契约

1. 灌入一份产品 FAQ（md），相关提问 `retrieve` 能命中正确块并带引用信息（代码强制：FakeEmbedding 下 top1 命中预设块）。
2. 不启用 RAG 时普通 Agent 不受影响（本单元无 Runtime 改动，天然成立；D3-int 接线保持该性质）。
3. 同一 source 重复 ingest（replace=True）不产生重复块。

## 边界契约

- knowledge 可 import core/extension/config/persistence + pydantic/标准库（pypdf/langchain_openai 懒加载）；禁止 import runtime/api/app.*/model 具体工厂/mcp/skill/memory/graph/multiagent。
- 最小 RAG：不做复杂重排/多库联邦（架构 §3.3）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/knowledge/` 全绿：parse 三格式与未知后缀报错；chunk 滑窗长度/重叠断言；ingest→retrieve 端到端（FAQ 命中）；replace 幂等；OpenAICompatibleEmbedding 缺 key → CONFIG_ERROR；search 的 score 降序与 top_k 截断；`alembic upgrade head` 含 0003。
2. 全量回归绿；`ruff check lubanagent/knowledge` 无错。

## 存疑项与决策

1. 向量存 DB JSON + Python cosine（决策 1）——规模上限已知，VectorStore Protocol 留扩展。
2. FakeEmbedding 相似度语义（决策 2）——只服务测试与零密钥演示，不用于生产检索质量承诺。
