"""检索侧：SqlKnowledgeConnector 实现 KnowledgeConnector；format_snippets_for_prompt 编号注入。"""

from lubanagent.extension.protocols import Snippet
from lubanagent.knowledge.embedding import EmbeddingProvider
from lubanagent.knowledge.store import KnowledgeStore


class SqlKnowledgeConnector:
    """基于 knowledge_chunks 表 + Python cosine 的 KnowledgeConnector 实现。"""

    def __init__(
        self, source: str, *, store: KnowledgeStore, embedder: EmbeddingProvider
    ) -> None:
        self._source = source
        self._store = store
        self._embedder = embedder

    async def retrieve(self, query: str, top_k: int) -> list[Snippet]:
        [query_embedding] = await self._embedder.embed([query])
        hits = await self._store.search(query_embedding, source=self._source, top_k=top_k)
        return [
            Snippet(
                content=hit["content"],
                source=hit["source"],
                score=hit["score"],
                metadata={**hit["metadata"], "chunk_index": hit["chunk_index"]},
            )
            for hit in hits
        ]


def format_snippets_for_prompt(snippets: list[Snippet]) -> str:
    """输出 "[1] content...\n[2] ..." 编号格式，模型回答按 [n] 引用。"""
    return "\n".join(
        f"[{index}] {snippet.content}" for index, snippet in enumerate(snippets, start=1)
    )
