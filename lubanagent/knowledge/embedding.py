"""EmbeddingProvider Protocol 与两个实现：OpenAI 兼容（懒加载）、Fake（确定性 hash 向量）。"""

import hashlib
import os
import re
from typing import Protocol, runtime_checkable

from lubanagent.core import ErrorCode, LubanError

_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")

# 高频功能字（单字 token）：在任何中文语料里到处出现，不具区分度。
# 滤掉后口语化查询与无关语料的 cosine 显著下降，阈值守门可校准。
_STOPWORDS = frozenset(
    "的了是在有和与或及等我你他她它们个不怎么什么为什么吗呢吧啊呀哦嘛么被把让向从对为以于即可也再就都很还又将要可以这个些那哪谁如何请谢不用没没有是否"
)


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedding:
    """OpenAI 兼容协议 embedding；langchain_openai 首次使用时懒加载。"""

    def __init__(
        self, *, model: str, base_url: str | None = None, api_key_env: str | None = None
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key_env = api_key_env

    async def embed(self, texts: list[str]) -> list[list[float]]:
        api_key = None
        if self._api_key_env is not None:
            api_key = os.environ.get(self._api_key_env)
            if not api_key:
                raise LubanError(
                    ErrorCode.CONFIG_ERROR,
                    f"环境变量 {self._api_key_env} 未设置，无法调用 embedding 接口",
                    details={"api_key_env": self._api_key_env},
                )
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                "未安装 langchain_openai",
                details={"hint": "pip install langchain_openai"},
            ) from exc
        kwargs: dict = {"model": self._model}
        if self._base_url is not None:
            kwargs["base_url"] = self._base_url
        if api_key is not None:
            kwargs["api_key"] = api_key
        client = OpenAIEmbeddings(**kwargs)
        return await client.aembed_documents(texts)


class FakeEmbedding:
    """确定性 token 集合 one-hot hash 向量：同文本同向量，关键词重叠越多 cosine 越高。

    两个去噪设计让 score_threshold 守门在零密钥演示下可校准：
    1. 默认 dim=2048——512 维在文档级语料（数百 chunk × 百级 token）下桶饱和，
       无关查询 cosine 虚高到 0.3+；
    2. 滤高频功能字（_STOPWORDS）——"怎么/什么/吗"类单字在任何中文语料都命中，
       口语化查询与无关文档的 cosine 被这些噪声 token 抬高。
    只服务测试与零密钥演示，不承诺生产检索质量。
    """

    def __init__(self, *, dim: int = 2048) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _TOKEN_RE.findall(text.lower()):
            if token in _STOPWORDS:
                continue
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "little") % self._dim] = 1.0
        return vector
