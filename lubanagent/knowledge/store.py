"""KnowledgeStore：knowledge_chunks 读写与 Python cosine 检索（向量库存 DB JSON，决策 1）。"""

import hashlib
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import ErrorCode, LubanError
from lubanagent.persistence import Database
from lubanagent.persistence.models.knowledge import KnowledgeChunkRow, KnowledgeSourceRow


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _to_dict(row: KnowledgeChunkRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "chunk_index": row.chunk_index,
        "content": row.content,
        "embedding": row.embedding,
        "metadata": row.chunk_metadata,
        "created_at": row.created_at,
    }


# search 的 top_k 钳制上限：下限 1（0/负数原会被切片静默吞掉全部结果），上限 100 是响应规模护栏。
# 取证存量调用方：API 层（api/schemas.py）已限 1..50，Agent 定义 KnowledgeConfig.top_k 默认 5——
# 全部落在 [1, 100] 内，钳制对存量行为零影响。
MAX_SEARCH_TOP_K = 100


class KnowledgeStore:
    """知识切块存取；search 全量读 + Python cosine 排序，小规模够用。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    @asynccontextmanager
    async def source_transaction(self, source: str) -> AsyncIterator[AsyncSession]:
        """同源跨实例写锁 + 事务；SQLite 先抢写锁，MySQL 使用连接级命名锁。"""
        dialect = self._db.engine.dialect.name
        lock_name = "luban:knowledge:" + hashlib.sha256(source.encode()).hexdigest()[:40]
        async with self._db.engine.connect() as connection:
            locked = False
            try:
                if dialect == "sqlite":
                    await connection.exec_driver_sql("BEGIN IMMEDIATE")
                elif dialect == "mysql":
                    acquired = await connection.scalar(
                        text("SELECT GET_LOCK(:name, 30)"), {"name": lock_name}
                    )
                    if acquired != 1:
                        raise LubanError(ErrorCode.CONFIG_ERROR, "获取知识源写锁失败")
                    locked = True
                    await connection.commit()
                else:
                    raise RuntimeError(f"知识源写锁不支持数据库: {dialect}")
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    yield session
                    await session.flush()
                    await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
            finally:
                if locked:
                    # 命名锁绑在当前物理连接，提交后仍须在归还连接池前释放。
                    await connection.scalar(text("SELECT RELEASE_LOCK(:name)"),
                                            {"name": lock_name})
                    await connection.rollback()

    async def _check_reservation(
        self, session: AsyncSession, source: str, reservation_id: int | None = None,
    ) -> None:
        row = await session.scalar(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.name == source
        ))
        if reservation_id is not None:
            if row is None or row.id != reservation_id or row.files:
                raise LubanError(ErrorCode.CONFIG_ERROR, f"知识源预留已失效: {source}")
        elif row is not None and not row.files:
            raise LubanError(ErrorCode.CONFIG_ERROR, f"知识源正在创建: {source}")

    async def write_chunks(
        self, source: str, chunks: list[dict], *, replace: bool,
        max_chunks: int, chunk_params: tuple[int, int] | None = None,
        reservation_id: int | None = None,
    ) -> int:
        """在同一写事务中校验预留所有权和配额，并替换或追加切块。"""
        async with self.source_transaction(source) as session:
            await self._check_reservation(session, source, reservation_id)
            existing = int(await session.scalar(
                select(func.count()).select_from(KnowledgeChunkRow).where(
                    KnowledgeChunkRow.source == source
                )
            ) or 0)
            if len(chunks) + (0 if replace else existing) > max_chunks:
                raise LubanError(ErrorCode.VALIDATION_ERROR, "灌库块数超过配额")
            if replace:
                await session.execute(delete(KnowledgeChunkRow).where(
                    KnowledgeChunkRow.source == source
                ))
            for chunk in chunks:
                session.add(KnowledgeChunkRow(
                    source=source, chunk_index=chunk["chunk_index"],
                    content=chunk["content"], embedding=chunk["embedding"],
                    chunk_metadata=chunk.get("metadata") or {},
                ))
            if chunk_params is not None:
                row = await session.scalar(select(KnowledgeSourceRow).where(
                    KnowledgeSourceRow.name == source
                ))
                if row is None:
                    raise LubanError(ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND,
                                     f"知识源不存在: {source}")
                row.chunk_size, row.overlap = chunk_params
                row.chunk_count = len(chunks)
            await session.flush()
        return len(chunks)

    async def add_chunks(self, source: str, chunks: list[dict]) -> int:
        """底层种子数据写入口；灌库请走有配额的 write_chunks。"""
        async with self.source_transaction(source) as session:
            await self._check_reservation(session, source)
            for chunk in chunks:
                session.add(KnowledgeChunkRow(
                    source=source, chunk_index=chunk["chunk_index"],
                    content=chunk["content"], embedding=chunk["embedding"],
                    chunk_metadata=chunk.get("metadata") or {},
                ))
        return len(chunks)

    async def delete_source(self, source: str) -> int:
        async with self.source_transaction(source) as session:
            await self._check_reservation(session, source)
            result = await session.execute(
                delete(KnowledgeChunkRow).where(KnowledgeChunkRow.source == source)
            )
            return result.rowcount or 0

    async def list_chunks(
        self, source: str | None = None, *, limit: int | None = None
    ) -> list[dict]:
        """按 (source, chunk_index) 排序返回 chunk dict；limit 作用于排序后。"""
        stmt = select(KnowledgeChunkRow).order_by(
            KnowledgeChunkRow.source, KnowledgeChunkRow.chunk_index
        )
        if source is not None:
            stmt = stmt.where(KnowledgeChunkRow.source == source)
        if limit is not None:
            stmt = stmt.limit(limit)
        async with self._db.session() as session:
            rows = (await session.scalars(stmt)).all()
            return [_to_dict(row) for row in rows]

    async def count_chunks(self, source: str | None = None) -> int:
        stmt = select(func.count()).select_from(KnowledgeChunkRow)
        if source is not None:
            stmt = stmt.where(KnowledgeChunkRow.source == source)
        async with self._db.session() as session:
            return int(await session.scalar(stmt) or 0)

    async def list_sources(self) -> list[str]:
        """库中有 chunk 的去重 source 清单（独立进程自举/运维排查用）。"""
        stmt = select(KnowledgeChunkRow.source).distinct()
        async with self._db.session() as session:
            return sorted(await session.scalars(stmt))

    async def search(
        self, embedding: list[float], *, source: str | None = None, top_k: int = 5
    ) -> list[dict]:
        """按 cosine 相似度降序取 top_k，返回带 "score" 的 chunk dict；空库返回空列表。

        top_k 钳制到 [1, MAX_SEARCH_TOP_K]：0/负数按 1 处理（返回最相关 1 条），
        超大值按 MAX_SEARCH_TOP_K 截断——不抛错、不吞结果。
        """
        top_k = min(max(int(top_k), 1), MAX_SEARCH_TOP_K)
        stmt = select(
            KnowledgeChunkRow.id,
            KnowledgeChunkRow.source,
            KnowledgeChunkRow.chunk_index,
            KnowledgeChunkRow.content,
            KnowledgeChunkRow.embedding,
            KnowledgeChunkRow.chunk_metadata,
        )
        if source is not None:
            stmt = stmt.where(KnowledgeChunkRow.source == source)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        hits = [
            {
                "id": row.id,
                "source": row.source,
                "chunk_index": row.chunk_index,
                "content": row.content,
                "metadata": row.chunk_metadata,
                "score": _cosine(embedding, row.embedding),
            }
            for row in rows
        ]
        hits.sort(key=lambda hit: hit["score"], reverse=True)
        return hits[:top_k]
