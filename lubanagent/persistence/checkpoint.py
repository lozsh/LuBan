"""LubanCheckpointer：自研 SQLAlchemy Saver，落 0001 已建的 checkpoints 表（契约决策 1）。

不引 langgraph PostgresSaver 等外部服务依赖；serde 用 JsonPlusSerializer。
async 接口为主；sync 接口按契约做兼容包装（独立短生命周期 engine，避免跨事件循环复用连接）。
"""

from __future__ import annotations  # 类内 list 方法与内置 list 同名，注解延迟求值

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any, TypeVar

import sqlalchemy as sa
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    RunnableConfig,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from lubanagent.persistence.models.checkpoint import CheckpointRow
from lubanagent.persistence.session import Database  # 直引定义模块，避免包级循环导入

T = TypeVar("T")


def _configurable(config: RunnableConfig) -> dict[str, Any]:
    return dict(config.get("configurable") or {})


class LubanCheckpointer(BaseCheckpointSaver):
    """checkpoints 表落库（自有 DB，不引外部服务）。

    表布局（0001）：(thread_id, checkpoint_ns, checkpoint_id) 主键
    + parent_checkpoint_id + type（serde 类型标记）+ checkpoint BLOB + metadata JSON。
    pending writes 不落库（无 writes 表）：aput_writes/put_writes 为 no-op，
    单 interrupt 节点的 resume 由 langgraph 的 null-resume 通道在内存内完成。
    """

    def __init__(self, db: Database) -> None:
        super().__init__(serde=JsonPlusSerializer())
        self._db = db

    # ------------------------------------------------------------ async 主路径

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self._aget_tuple(self._db, config)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await self._aput(self._db, config, checkpoint, metadata)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for tup in await self._alist(self._db, config, filter=filter, before=before, limit=limit):
            yield tup

    async def adelete_thread(self, thread_id: str) -> None:
        await self._adelete_thread(self._db, thread_id)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Any,
        task_id: str,
        task_path: str = "",
    ) -> None:
        """pending writes 不落库（无 writes 表）；单 interrupt 的 resume 不依赖它。"""
        return None

    # ------------------------------------------------------------ sync 兼容包装

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._run_blocking(lambda db: self._aget_tuple(db, config))

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._run_blocking(lambda db: self._aput(db, config, checkpoint, metadata))

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        tuples = self._run_blocking(
            lambda db: self._alist(db, config, filter=filter, before=before, limit=limit)
        )
        return iter(tuples)

    def delete_thread(self, thread_id: str) -> None:
        self._run_blocking(lambda db: self._adelete_thread(db, thread_id))

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Any,
        task_id: str,
        task_path: str = "",
    ) -> None:
        return None

    # ------------------------------------------------------------ 内部实现

    async def _aget_tuple(self, db: Database, config: RunnableConfig) -> CheckpointTuple | None:
        conf = _configurable(config)
        thread_id = conf.get("thread_id")
        if not thread_id:
            return None
        ns = conf.get("checkpoint_ns", "")
        checkpoint_id = conf.get("checkpoint_id")
        async with db.session() as session:
            stmt = sa.select(CheckpointRow).where(
                CheckpointRow.thread_id == thread_id,
                CheckpointRow.checkpoint_ns == ns,
            )
            if checkpoint_id:
                stmt = stmt.where(CheckpointRow.checkpoint_id == checkpoint_id)
            else:
                # checkpoint_id 为 uuid6（时间有序），字典序倒序即最新
                stmt = stmt.order_by(CheckpointRow.checkpoint_id.desc()).limit(1)
            row = (await session.execute(stmt)).scalars().first()
        if row is None:
            return None
        parent_config: RunnableConfig | None = None
        if row.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint=self.serde.loads_typed((row.type, row.checkpoint)),
            metadata=dict(row.metadata_ or {}),
            parent_config=parent_config,
            pending_writes=None,
        )

    async def _aput(
        self,
        db: Database,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        conf = _configurable(config)
        ns = conf.get("checkpoint_ns", "")
        serde_type, blob = self.serde.dumps_typed(checkpoint)
        async with db.session() as session:
            await session.merge(
                CheckpointRow(
                    thread_id=conf["thread_id"],
                    checkpoint_ns=ns,
                    checkpoint_id=checkpoint["id"],
                    parent_checkpoint_id=conf.get("checkpoint_id"),
                    type=serde_type,
                    checkpoint=blob,
                    metadata_=dict(metadata),
                )
            )
        return {
            "configurable": {
                "thread_id": conf["thread_id"],
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def _alist(
        self,
        db: Database,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None,
        before: RunnableConfig | None,
        limit: int | None,
    ) -> list[CheckpointTuple]:
        conf = _configurable(config) if config else {}
        async with db.session() as session:
            stmt = sa.select(CheckpointRow)
            if conf.get("thread_id"):
                stmt = stmt.where(CheckpointRow.thread_id == conf["thread_id"])
            if "checkpoint_ns" in conf:
                stmt = stmt.where(CheckpointRow.checkpoint_ns == conf["checkpoint_ns"])
            before_id = _configurable(before).get("checkpoint_id") if before else None
            if before_id:
                stmt = stmt.where(CheckpointRow.checkpoint_id < before_id)
            stmt = stmt.order_by(CheckpointRow.checkpoint_id.desc())
            if limit:
                stmt = stmt.limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
        tuples: list[CheckpointTuple] = []
        for row in rows:
            metadata = dict(row.metadata_ or {})
            if filter and any(metadata.get(key) != value for key, value in filter.items()):
                continue
            parent_config = None
            if row.parent_checkpoint_id:
                parent_config = {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
            tuples.append(
                CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": row.thread_id,
                            "checkpoint_ns": row.checkpoint_ns,
                            "checkpoint_id": row.checkpoint_id,
                        }
                    },
                    checkpoint=self.serde.loads_typed((row.type, row.checkpoint)),
                    metadata=metadata,
                    parent_config=parent_config,
                    pending_writes=None,
                )
            )
        return tuples

    async def _adelete_thread(self, db: Database, thread_id: str) -> None:
        async with db.session() as session:
            await session.execute(
                sa.delete(CheckpointRow).where(CheckpointRow.thread_id == thread_id)
            )

    def _run_blocking(self, op: Callable[[Database], Awaitable[T]]) -> T:
        """sync 兼容层：独立短生命周期 engine，避免跨事件循环复用连接。"""

        async def _main() -> T:
            tmp = Database(str(self._db.engine.url))
            try:
                return await op(tmp)
            finally:
                await tmp.close()

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_main())
        # 已在事件循环内：开独立线程跑新循环（LangGraph 兼容路径，非常用）
        box: dict[str, Any] = {}

        def _target() -> None:
            try:
                box["value"] = asyncio.run(_main())
            except BaseException as exc:  # 跨线程回传原异常
                box["error"] = exc

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join()
        if "error" in box:
            raise box["error"]
        return box.get("value")


__all__ = ["LubanCheckpointer"]
