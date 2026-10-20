"""checkpointer CRUD：put→get_tuple 往返、list 按 thread 查、delete_thread 清空、sync 包装冒烟。"""

import sqlalchemy as sa
from langgraph.checkpoint.base import empty_checkpoint

from lubanagent.graph import LubanCheckpointer
from lubanagent.persistence import Database
from lubanagent.persistence.models.checkpoint import CheckpointRow


def _config(thread_id: str, checkpoint_id: str | None = None) -> dict:
    configurable: dict = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint(checkpoint_id: str) -> dict:
    checkpoint = empty_checkpoint()
    checkpoint["id"] = checkpoint_id
    return checkpoint


def _metadata(step: int) -> dict:
    return {"source": "loop", "step": step, "parents": {}}


async def test_put_get_tuple_roundtrip(db: Database):
    saver = LubanCheckpointer(db)
    checkpoint = _checkpoint("00000000-0000-6000-8000-000000000001")
    metadata = _metadata(1)
    config = _config("t-1")

    new_config = await saver.aput(config, checkpoint, metadata, {})
    assert new_config["configurable"]["checkpoint_id"] == checkpoint["id"]

    tup = await saver.aget_tuple(_config("t-1"))
    assert tup is not None
    assert tup.checkpoint["id"] == checkpoint["id"]
    assert tup.metadata["step"] == 1
    assert tup.parent_config is None

    # 带 parent 的第二版本：最新 checkpoint 指向 parent
    checkpoint2 = _checkpoint("00000000-0000-6000-8000-000000000002")
    await saver.aput(new_config, checkpoint2, _metadata(2), {})
    tup2 = await saver.aget_tuple(_config("t-1"))
    assert tup2.checkpoint["id"] == checkpoint2["id"]
    assert tup2.parent_config["configurable"]["checkpoint_id"] == checkpoint["id"]

    # 指定 id 精确取
    tup1 = await saver.aget_tuple(_config("t-1", checkpoint["id"]))
    assert tup1.checkpoint["id"] == checkpoint["id"]

    # 不存在的线程
    assert await saver.aget_tuple(_config("t-ghost")) is None


async def test_list_by_thread(db: Database):
    saver = LubanCheckpointer(db)
    for index in range(1, 4):
        await saver.aput(
            _config("t-list"),
            _checkpoint(f"00000000-0000-6000-8000-00000000000{index}"),
            _metadata(index),
            {},
        )
    await saver.aput(
        _config("t-other"), _checkpoint("00000000-0000-6000-8000-000000000009"), _metadata(1), {}
    )

    tuples = [tup async for tup in saver.alist(_config("t-list"))]
    assert len(tuples) == 3  # 只含本 thread
    ids = [tup.checkpoint["id"] for tup in tuples]
    assert ids == sorted(ids, reverse=True)  # 最新在前

    limited = [tup async for tup in saver.alist(_config("t-list"), limit=2)]
    assert len(limited) == 2


async def test_delete_thread(db: Database):
    saver = LubanCheckpointer(db)
    await saver.aput(
        _config("t-del"), _checkpoint("00000000-0000-6000-8000-000000000001"), _metadata(1), {}
    )
    await saver.adelete_thread("t-del")
    assert await saver.aget_tuple(_config("t-del")) is None
    async with db.session() as session:
        count = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(CheckpointRow)
                .where(CheckpointRow.thread_id == "t-del")
            )
        ).scalar()
    assert count == 0


def test_sync_wrappers(tmp_path):
    """sync get_tuple/put/list/delete_thread 兼容包装（无事件循环路径）。"""
    import asyncio

    db_path = tmp_path / "sync.db"

    async def _seed() -> None:
        db = Database(f"sqlite+aiosqlite:///{db_path}")
        from lubanagent.persistence import Base

        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        saver = LubanCheckpointer(db)
        await saver.aput(
            _config("t-sync"),
            _checkpoint("00000000-0000-6000-8000-000000000001"),
            _metadata(1),
            {},
        )
        await db.close()

    asyncio.run(_seed())

    db = Database(f"sqlite+aiosqlite:///{db_path}")
    saver = LubanCheckpointer(db)
    tup = saver.get_tuple(_config("t-sync"))
    assert tup is not None
    assert tup.checkpoint["id"] == "00000000-0000-6000-8000-000000000001"
    assert len(list(saver.list(_config("t-sync")))) == 1
    saver.delete_thread("t-sync")
    assert saver.get_tuple(_config("t-sync")) is None
