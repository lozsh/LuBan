"""ExportFailureRepository：record / list_pending / list_given_up /
mark_retry_failed / delete 重试消费闭环一轮。"""

from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.persistence.repositories import ExportFailureRepository


async def test_record_list_delete_roundtrip(session: AsyncSession) -> None:
    repo = ExportFailureRepository(session)
    await repo.record("langfuse", "run-1", "model_call_completed", {"k": 1}, "boom-1")
    await repo.record("langfuse", "run-2", "end_trace", {"k": 2}, "boom-2")
    await repo.record("langsmith", "run-3", "tool_call_started", {"k": 3}, "boom-3")

    pending = await repo.list_pending()
    assert [p["run_id"] for p in pending] == ["run-1", "run-2", "run-3"]  # id 升序
    assert pending[0]["backend"] == "langfuse"
    assert pending[0]["event_type"] == "model_call_completed"
    assert pending[0]["payload"] == {"k": 1}
    assert pending[0]["error"] == "boom-1"
    assert pending[0]["retry_count"] == 0  # 新记录未尝试重试
    assert pending[0]["retried_at"] is None
    assert pending[0]["created_at"] is not None

    # backend 过滤 + limit
    langfuse_only = await repo.list_pending(backend="langfuse")
    assert [p["run_id"] for p in langfuse_only] == ["run-1", "run-2"]
    limited = await repo.list_pending(limit=2)
    assert len(limited) == 2

    # 回放成功 → delete 清除留痕
    await repo.delete([pending[0]["id"]])
    remaining = await repo.list_pending()
    assert [p["run_id"] for p in remaining] == ["run-2", "run-3"]

    # 空列表 no-op
    await repo.delete([])
    assert len(await repo.list_pending()) == 2


async def test_mark_retry_failed_counts_attempt(session: AsyncSession) -> None:
    repo = ExportFailureRepository(session)
    await repo.record("langfuse", "run-1", "model_call_started", {"k": 1}, "boom")
    row = (await repo.list_pending())[0]

    await repo.mark_retry_failed([row["id"]])  # 一次重试失败
    after = next(p for p in await repo.list_pending() if p["id"] == row["id"])
    assert after["retry_count"] == 1
    assert after["retried_at"] is not None  # 最后尝试时间已记

    # 空列表 no-op
    await repo.mark_retry_failed([])
    still = next(p for p in await repo.list_pending() if p["id"] == row["id"])
    assert still["retry_count"] == 1


async def test_given_up_after_max_attempts(session: AsyncSession) -> None:
    repo = ExportFailureRepository(session)
    await repo.record("langfuse", "run-1", "model_call_started", {"k": 1}, "boom")
    row = (await repo.list_pending())[0]

    for _ in range(3):  # 默认上限 3 次
        await repo.mark_retry_failed([row["id"]])

    assert await repo.list_pending() == []  # 达上限不再待重试
    given_up = await repo.list_given_up()
    assert len(given_up) == 1  # 行保留供运维查询
    assert given_up[0]["run_id"] == "run-1"
    assert given_up[0]["retry_count"] == 3
    assert given_up[0]["retried_at"] is not None

    # 上限可调宽：放宽后重新可重试
    assert len(await repo.list_pending(max_attempts=5)) == 1
    assert await repo.list_given_up(max_attempts=5) == []

    # backend 过滤
    await repo.record("langsmith", "run-2", "end_trace", {}, "boom-2")
    for _ in range(3):
        await repo.mark_retry_failed(
            [next(p["id"] for p in await repo.list_pending(backend="langsmith"))]
        )
    assert [g["backend"] for g in await repo.list_given_up()] == ["langsmith", "langfuse"]
