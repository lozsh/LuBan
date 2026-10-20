"""产品 FAQ 知识源装配：从 Mock 业务服务拉产品数据，灌入 KnowledgeStore。

实现 KnowledgeConnector Protocol：retrieve(query) → Snippet 列表。
使用 FakeEmbedding（零密钥），客服示例的 RAG 演示够用。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from lubanagent.config import get_settings
from lubanagent.extension import KnowledgeConnector, default_registry
from lubanagent.knowledge import (
    EmbeddingProvider,
    KnowledgeStore,
    SqlKnowledgeConnector,
    chunk_text,
)

# 降级种子在本领地内（app/knowledge_sources/），不跨领地读 demos/ 文件
_SEED_FILE = Path(__file__).resolve().parent / "product_faq_seed.json"
_DEFAULT_BIZ_URL = "http://localhost:9100"
_SOURCE = "product-faq"


def _biz_url() -> str:
    # 统一走 Settings.biz_base_url(env: BIZ_BASE_URL), 不直接读环境变量
    return (get_settings().biz_base_url or _DEFAULT_BIZ_URL).rstrip("/")


def _load_products() -> list[dict]:
    """优先从 Mock 服务拉，失败则降级读本地 JSON（离线测试可跑）。"""
    try:
        resp = httpx.get(f"{_biz_url()}/products/search", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
    except httpx.RequestError:
        pass
    # 降级：本地种子数据（与 demos Mock 服务同源拷贝，离线可跑）
    if _SEED_FILE.is_file():
        return json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    return []


async def ingest_product_faq(store: KnowledgeStore, embedder: EmbeddingProvider) -> int:
    """拉产品数据 → 切块 → embed → 入库（source=product-faq，打 ingested_at 时间戳）。返回块数。"""
    await store.delete_source(_SOURCE)
    products = _load_products()
    chunks: list[str] = []
    for item in products:
        text = f"{item.get('name', '')}：{item.get('faq', '')}"
        chunks.extend(chunk_text(text, chunk_size=200, overlap=20))
    if not chunks:
        return 0
    embeddings = await embedder.embed(chunks)
    records = [
        {
            "chunk_index": i,
            "content": chunk,
            "embedding": emb,
            # ingested_at 供 max_age_days 超龄过滤（防错答守门）
            "metadata": {"ingested_at": datetime.now(UTC).isoformat()},
        }
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True))
    ]
    return await store.add_chunks(_SOURCE, records)


def make_product_faq_connector(
    store: KnowledgeStore, embedder: EmbeddingProvider
) -> KnowledgeConnector:
    """构造并注册 product-faq 知识源 connector。"""
    connector = SqlKnowledgeConnector(_SOURCE, store=store, embedder=embedder)
    default_registry.register_knowledge(_SOURCE, connector)
    return connector


__all__ = ["ingest_product_faq", "make_product_faq_connector"]
