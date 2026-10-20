"""独立进程（CLI eval 等）的知识源自举。

知识源连接器通常在用户领地装配期注册（app/main.py 构造 store/embedder 后
register_knowledge）——API 服务进程天然有这套装配，独立 CLI 进程没有。
本函数按库中已有 chunk 的 source 重建通用 SqlKnowledgeConnector：

- 种子源（product-faq/project-docs）与工作台托管源的 connector 本就是裸
  SqlKnowledgeConnector（构造时只传 source key + store + embedder），
  重建后检索行为完全一致；
- 已在注册表中的 source 不覆盖——EXTENSION_PACKAGES 扫描注册的自定义
  connector（含非 SQL 检索逻辑）保持原样；
- embedder 选择与 factory 同语义（配了 embedding_model 走 OpenAI 兼容，
  否则 FakeEmbedding），保证查询向量与灌入向量同空间。

放 runtime 层的原因：cli/evals 的分层契约禁止 import knowledge，runtime
合法依赖 knowledge 且是连接器的消费方；cli 可消费 runtime（评测入口契约）。
"""

from lubanagent.config import Settings
from lubanagent.extension import Registry, default_registry
from lubanagent.knowledge import (
    FakeEmbedding,
    KnowledgeStore,
    OpenAICompatibleEmbedding,
    SqlKnowledgeConnector,
)
from lubanagent.persistence import Database


async def restore_knowledge_sources(
    db: Database, settings: Settings, *, registry: Registry | None = None
) -> int:
    """按库中已有 chunk 重建未注册的 SQL 知识源连接器，返回新建条数。

    仅覆盖「有 chunk 数据、注册表无连接器」的 source（典型：CLI eval 进程
    里的种子源/托管源）。库中无该 source 的 chunk 时不注册——引用它的
    Agent 仍会在运行期得到「知识源未注册」CONFIG_ERROR（拼写错误不静默吞）。
    """
    reg = registry if registry is not None else default_registry
    embedder = (
        OpenAICompatibleEmbedding(
            model=settings.embedding_model or "",
            base_url=settings.embedding_base_url,
            api_key_env=settings.embedding_api_key_env,
        )
        if settings.embedding_model
        else FakeEmbedding()
    )
    store = KnowledgeStore(db)
    restored = 0
    for source in await store.list_sources():
        if source in reg.list_knowledge():
            continue
        reg.register_knowledge(
            source, SqlKnowledgeConnector(source, store=store, embedder=embedder)
        )
        restored += 1
    return restored
