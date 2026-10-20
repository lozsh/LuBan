"""KnowledgeStore.search 的 top_k 钳制：[1, MAX_SEARCH_TOP_K]，防 0/负数吞结果、超大值失控。"""

from lubanagent.knowledge import FakeEmbedding, KnowledgeStore
from lubanagent.knowledge.store import MAX_SEARCH_TOP_K
from lubanagent.persistence import Database


async def _seed(store: KnowledgeStore, embedder: FakeEmbedding, texts: list[str]) -> None:
    """写入 texts 作为同 source 的 chunk，embedding 用 FakeEmbedding 保持确定性。"""
    await store.add_chunks(
        "search-clamp",
        [
            {
                "chunk_index": index,
                "content": text,
                "embedding": (await embedder.embed([text]))[0],
                "metadata": {},
            }
            for index, text in enumerate(texts)
        ],
    )


async def test_search_top_k_zero_returns_best_one(
    db: Database, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    """top_k=0 不再静默吞掉全部结果（旧行为 hits[:0]=[]），按 1 处理返回最相关 1 条。"""
    await _seed(store, embedder, ["退货政策七天无理由", "发票开具流程", "会员积分规则"])
    [query_embedding] = await embedder.embed(["退货政策"])

    hits = await store.search(query_embedding, top_k=0)
    assert len(hits) == 1
    assert hits[0]["content"] == "退货政策七天无理由"


async def test_search_top_k_negative_returns_best_one(
    db: Database, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    """负数 top_k 同样按 1 处理，不抛错。"""
    await _seed(store, embedder, ["退货政策七天无理由", "发票开具流程", "会员积分规则"])
    [query_embedding] = await embedder.embed(["退货政策"])

    hits = await store.search(query_embedding, top_k=-5)
    assert len(hits) == 1
    assert hits[0]["content"] == "退货政策七天无理由"


async def test_search_top_k_huge_clamped_to_max(
    db: Database, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    """超大 top_k 钳到 MAX_SEARCH_TOP_K：库里只有 3 条就返回 3 条，不异常。"""
    await _seed(store, embedder, ["退货政策七天无理由", "发票开具流程", "会员积分规则"])
    [query_embedding] = await embedder.embed(["退货政策"])

    hits = await store.search(query_embedding, top_k=10_000)
    assert len(hits) == 3
    assert MAX_SEARCH_TOP_K == 100  # 上限取证决策：API 层已限 1..50，100 留足余量


async def test_search_top_k_in_range_unchanged(
    db: Database, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    """正常区间 top_k（存量调用方全部落在此区间）行为不变：按分数降序截断。"""
    await _seed(store, embedder, ["退货政策七天无理由", "发票开具流程", "会员积分规则"])
    [query_embedding] = await embedder.embed(["退货政策"])

    hits = await store.search(query_embedding, top_k=2)
    assert len(hits) == 2
    assert hits[0]["score"] >= hits[1]["score"]
    assert hits[0]["content"] == "退货政策七天无理由"
