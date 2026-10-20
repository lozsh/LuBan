"""retrieve：FAQ 命中（FakeEmbedding 下 top1 预设块）、score 降序与 top_k 截断、空库、编号格式。"""

from pathlib import Path

import pytest

from lubanagent.extension.protocols import KnowledgeConnector, Snippet
from lubanagent.knowledge import (
    FakeEmbedding,
    KnowledgeStore,
    SqlKnowledgeConnector,
    chunk_text,
    format_snippets_for_prompt,
    ingest_file,
)

# 三段主题各约 260 字（总长 > 500），保证默认滑窗在段落边界断出多个切块。
_TUI = "退货政策：支持七天无理由退货，退货运费由商家承担，退款原路返回，退货申请在订单页提交。"
_FA = "发货时间：工作日十六点前下单当日发出，其余订单次日发出，偏远地区顺延，节假日另行通知。"
_BAO = "保修说明：整机保修一年，核心部件保修三年，人为损坏不在保修范围内，维修免收人工费用。"
FAQ_TEXT = "# 产品 FAQ\n\n" + "\n\n".join([_TUI * 6, _FA * 6, _BAO * 6]) + "\n"


@pytest.fixture
async def faq_store(tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding):
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    count = await ingest_file(path, source="faq", store=store, embedder=embedder)
    assert count == len(chunk_text(FAQ_TEXT)) >= 2
    return store


async def test_retrieve_faq_hit_top1(
    faq_store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    connector = SqlKnowledgeConnector("faq", store=faq_store, embedder=embedder)
    snippets = await connector.retrieve("如何申请退货退款", top_k=3)
    assert snippets
    assert "退货" in snippets[0].content
    assert snippets[0].source == "faq"
    assert snippets[0].score > 0
    assert "chunk_index" in snippets[0].metadata


async def test_retrieve_implements_knowledge_connector_protocol(
    faq_store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    connector = SqlKnowledgeConnector("faq", store=faq_store, embedder=embedder)
    assert isinstance(connector, KnowledgeConnector)


async def test_search_scores_descending_and_top_k_truncated(
    faq_store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    [query_embedding] = await embedder.embed(["退货退款流程"])
    hits = await faq_store.search(query_embedding, source="faq", top_k=2)
    assert len(hits) == 2
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)

    all_hits = await faq_store.search(query_embedding, source="faq", top_k=100)
    assert len(all_hits) >= 2
    assert [hit["id"] for hit in hits] == [hit["id"] for hit in all_hits[:2]]


async def test_search_empty_store_returns_empty_list(
    store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    [query_embedding] = await embedder.embed(["随便问问"])
    assert await store.search(query_embedding, source="faq") == []


async def test_retrieve_empty_source_returns_empty_list(
    store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    connector = SqlKnowledgeConnector("faq", store=store, embedder=embedder)
    assert await connector.retrieve("退货", top_k=5) == []


def test_format_snippets_for_prompt_numbered() -> None:
    snippets = [
        Snippet(content="第一条内容", source="faq", score=0.9),
        Snippet(content="第二条内容", source="faq", score=0.5),
    ]
    assert format_snippets_for_prompt(snippets) == "[1] 第一条内容\n[2] 第二条内容"
    assert format_snippets_for_prompt([]) == ""
