"""Embedding：FakeEmbedding 确定性与相似度语义；OpenAICompatibleEmbedding 缺 key 报错。"""

import math

import pytest

from lubanagent.core import ErrorCode, LubanError
from lubanagent.knowledge import FakeEmbedding, OpenAICompatibleEmbedding


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def test_fake_embedding_deterministic() -> None:
    embedder = FakeEmbedding()
    first = await embedder.embed(["退货政策说明"])
    second = await embedder.embed(["退货政策说明"])
    assert first == second
    assert len(first[0]) == 2048


async def test_fake_embedding_keyword_overlap_scores_higher() -> None:
    embedder = FakeEmbedding()
    [query, related, unrelated] = await embedder.embed(
        ["如何退货", "退货流程：七天无理由退货", "发货时间：下单后 48 小时内发出"]
    )
    assert _cosine(query, related) > _cosine(query, unrelated)


async def test_fake_embedding_batch_length() -> None:
    embedder = FakeEmbedding(dim=32)
    vectors = await embedder.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(vector) == 32 for vector in vectors)


async def test_openai_embedding_missing_api_key_env_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LUBAN_TEST_MISSING_KEY", raising=False)
    embedder = OpenAICompatibleEmbedding(
        model="text-embedding-3-small", api_key_env="LUBAN_TEST_MISSING_KEY"
    )
    with pytest.raises(LubanError) as exc_info:
        await embedder.embed(["hello"])
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details["api_key_env"] == "LUBAN_TEST_MISSING_KEY"
