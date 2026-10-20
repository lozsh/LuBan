"""部门项目文档知识源：把 LubanAgent 自身的产品/设计文档灌入 KnowledgeStore。

与 product_faq 同款装配模式：文件清单固定在 _DOC_FILES（部门项目=本仓库核心文档），
chunk 后经 FakeEmbedding 向量化入库（打 ingested_at，供 max_age_days 守门）。
作为"部门项目小助手"（dept-project Agent）的知识底座，展示纯 RAG 问答 + 防错答守门
的完整配置范本：换一个部门项目只需改 _DOC_FILES 清单与 Agent YAML。
"""

from datetime import UTC, datetime
from pathlib import Path

from lubanagent.extension import KnowledgeConnector, default_registry
from lubanagent.knowledge import (
    EmbeddingProvider,
    KnowledgeStore,
    SqlKnowledgeConnector,
    chunk_text,
)

# 部门项目文档清单（相对仓库根）。产品定位/技术方案/架构/使用手册/协作约定等核心文档
# （使用手册随 website/ 文档站维护，与开发文档 docs/ 分离）
_DOC_FILES = [
    "docs/product-specs/LubanAgent-prd.md",
    "docs/design-docs/LubanAgent-technical-proposal.md",
    "docs/design-docs/LubanAgent-architecture-design.md",
    "website/manual/index.md",
    "website/manual/architecture.md",
    "website/manual/getting-started.md",
    "website/manual/glossary.md",
    "AGENTS.md",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = "project-docs"
# 文档切块：中文 token 粒度下 300 字符窗口 + 30 重叠（token 重叠式 embedder 的均衡值）
_CHUNK_SIZE = 300
_CHUNK_OVERLAP = 30


def _doc_paths() -> list[Path]:
    """清单内存在的文档路径（缺失文件跳过，不炸启动）。"""
    return [path for path in (_REPO_ROOT / f for f in _DOC_FILES) if path.is_file()]


async def ingest_project_docs(store: KnowledgeStore, embedder: EmbeddingProvider) -> int:
    """逐文档 解析 → 切块 → embed → 入库（source=project-docs，整源 replace）。返回块数。"""
    paths = _doc_paths()
    if not paths:
        return 0
    await store.delete_source(_SOURCE)
    chunks: list[str] = []
    files: list[str] = []  # 与 chunks 一一对应的来源文件名（进 metadata 供溯源）
    for path in paths:
        text = path.read_text(encoding="utf-8")
        pieces = chunk_text(text, chunk_size=_CHUNK_SIZE, overlap=_CHUNK_OVERLAP)
        chunks.extend(pieces)
        files.extend([path.name] * len(pieces))
    if not chunks:
        return 0
    embeddings = await embedder.embed(chunks)
    stamp = datetime.now(UTC).isoformat()
    records = [
        {
            "chunk_index": i,
            "content": chunk,
            "embedding": emb,
            # ingested_at 供 max_age_days 超龄过滤；doc 记录来源文档（[n] 引用溯源）
            "metadata": {"doc": doc, "ingested_at": stamp},
        }
        for i, (chunk, emb, doc) in enumerate(
            zip(chunks, embeddings, files, strict=True)
        )
    ]
    return await store.add_chunks(_SOURCE, records)


def make_project_docs_connector(
    store: KnowledgeStore, embedder: EmbeddingProvider
) -> KnowledgeConnector:
    """构造并注册 project-docs 知识源 connector。"""
    connector = SqlKnowledgeConnector(_SOURCE, store=store, embedder=embedder)
    default_registry.register_knowledge(_SOURCE, connector)
    return connector


__all__ = ["ingest_project_docs", "make_project_docs_connector"]
