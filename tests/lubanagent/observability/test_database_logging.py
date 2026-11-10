"""日志队列有界、独立入库、保留期与失败信号，不阻断业务。"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import lubanagent.observability.logging as logging_module
from lubanagent.config import configure_logging
from lubanagent.config.logging import CorrelationFilter
from lubanagent.core.log_context import current_log_context, log_context
from lubanagent.observability.logging import DatabaseLogWriter, sanitize_log_text
from lubanagent.persistence.repositories.log import ApplicationLogRepository


def test_context_restores_and_adds_json_fields(capsys):
    configure_logging(fmt="json")
    assert current_log_context() == {}
    with log_context(session_id="s", run_id="r", trace_id="trace-r"):
        logging.getLogger("test.context").info("hello")
        with log_context(run_id="child"):
            assert current_log_context()["run_id"] == "child"
        assert current_log_context()["run_id"] == "r"
    assert current_log_context() == {}
    assert '"trace_id": "trace-r"' in capsys.readouterr().err


async def test_bounded_queue_flush_and_cleanup(db):
    writer = DatabaseLogWriter(db, capacity=2, retention_days=1)
    for index in range(3):
        record = logging.makeLogRecord(
            {"name": "app.test", "levelno": 20, "levelname": "INFO", "msg": f"message{index}"}
        )
        record.session_id = "session"
        record.run_id = "run"
        writer.enqueue(record)
    assert writer.dropped_count == 1
    await writer.flush()
    async with db.session() as session:
        items, total = await ApplicationLogRepository(session).list_logs(session_id="session")
    assert total == 2 and items[0]["message"].startswith("message")
    await writer.close()
    writer.enqueue(logging.makeLogRecord({"msg": "ignored"}))
    assert writer._queue.empty()


async def test_log_writer_failure_does_not_raise(db, monkeypatch, capsys):
    writer = DatabaseLogWriter(db, capacity=1)
    writer.enqueue(logging.makeLogRecord({"msg": "x", "name": "test"}))

    async def fail(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ApplicationLogRepository, "add_batch", fail)
    await writer.flush()
    assert writer.write_failures >= 1 and writer.dropped_count == 1
    assert "RuntimeError" in capsys.readouterr().err


@pytest.mark.parametrize("uptime", [0.0, 10.0, 3599.0, 7200.0])
async def test_log_retention_delete_before(db, monkeypatch, uptime):
    # Replace this module's clock, not time.monotonic globally (asyncio uses it).
    monkeypatch.setattr(logging_module, "time", SimpleNamespace(monotonic=lambda: uptime))
    writer = DatabaseLogWriter(db, retention_days=1)
    record = logging.makeLogRecord({"name": "test.old", "levelno": 20, "msg": "old"})
    record.created = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    writer.enqueue(record)
    await writer.flush()
    async with db.session() as session:
        _, total = await ApplicationLogRepository(session).list_logs()
    assert total == 0
    assert writer._last_cleanup == uptime
    assert writer.dropped_count == writer.write_failures == 0
    await writer.close()


async def test_log_retention_cleans_first_flush_then_hourly(db, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(logging_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original = ApplicationLogRepository.delete_before
    calls = []

    async def tracked_cleanup(repository, cutoff):
        calls.append(clock.now)
        return await original(repository, cutoff)

    monkeypatch.setattr(ApplicationLogRepository, "delete_before", tracked_cleanup)
    writer = DatabaseLogWriter(db, retention_days=1)
    writer.enqueue(logging.makeLogRecord({"name": "test.fresh", "msg": "fresh"}))
    await writer.flush()
    assert calls == [0.0]
    assert writer._last_cleanup == 0.0

    clock.now = 0.1
    await writer.flush()
    clock.now = 3599.0
    old = logging.makeLogRecord({"name": "test.old", "msg": "old"})
    old.created = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    writer.enqueue(old)
    await writer.flush()
    assert calls == [0.0]
    async with db.session() as session:
        _, total = await ApplicationLogRepository(session).list_logs()
    assert total == 2

    clock.now = 3600.0
    await writer.flush()
    assert calls == [0.0, 3600.0]
    async with db.session() as session:
        items, total = await ApplicationLogRepository(session).list_logs()
    assert total == 1 and items[0]["message"] == "fresh"
    assert writer.dropped_count == writer.write_failures == 0
    await writer.close()
    assert calls == [0.0, 3600.0]


async def test_log_retention_cleanup_failure_retries_without_dropping_logs(db, monkeypatch):
    monkeypatch.setattr(logging_module, "time", SimpleNamespace(monotonic=lambda: 10.0))
    original = ApplicationLogRepository.delete_before
    calls = 0

    async def fail_once(repository, cutoff):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cleanup unavailable")
        return await original(repository, cutoff)

    monkeypatch.setattr(ApplicationLogRepository, "delete_before", fail_once)
    writer = DatabaseLogWriter(db, retention_days=1)
    old = logging.makeLogRecord({"name": "test.old", "msg": "old"})
    old.created = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    writer.enqueue(old)
    writer.enqueue(logging.makeLogRecord({"name": "test.fresh", "msg": "fresh"}))
    await writer.flush()
    assert calls == 1 and writer._last_cleanup is None
    assert writer.dropped_count == 0 and writer.write_failures == 1
    async with db.session() as session:
        _, total = await ApplicationLogRepository(session).list_logs()
    assert total == 2

    await writer.flush()
    assert calls == 2 and writer._last_cleanup == 10.0
    async with db.session() as session:
        items, total = await ApplicationLogRepository(session).list_logs()
    assert total == 1 and items[0]["message"] == "fresh"
    assert writer.dropped_count == 0 and writer.write_failures == 1
    await writer.close()
    assert calls == 2


def test_secrets_redacted_and_text_bounded():
    assert "secret" not in sanitize_log_text("password=secret")
    assert "key" not in sanitize_log_text("Bearer key")
    assert len(sanitize_log_text("x" * 40000)) == 32768
    assert len(sanitize_log_text("中" * 40000).encode("utf-8")) <= 32768
    assert sanitize_log_text("https://[broken") == "[REDACTED]"


async def test_extra_is_bounded_sanitized_and_detached(db):
    writer = DatabaseLogWriter(db)
    nested = {"password": "never-store-this", "value": "before"}
    cyclic = {"nested": nested}
    cyclic["self"] = cyclic
    record = logging.makeLogRecord({"msg": "bounded", "name": "test.extra", "data": cyclic})
    writer.enqueue(record)
    nested["value"] = "after"
    record = logging.makeLogRecord({"msg": "large", "name": "test.extra", "payload": "中" * 100000})
    writer.enqueue(record)
    await writer.flush()
    async with db.session() as session:
        items, total = await ApplicationLogRepository(session).list_logs(logger="test.extra")
    assert total == 2
    serialized = json.dumps(items, default=str, ensure_ascii=False)
    assert "never-store-this" not in serialized
    assert "after" not in serialized
    assert "before" in serialized
    assert all(len(json.dumps(item["extra"]).encode("utf-8")) < 16384 for item in items)
    assert next(item for item in items if item["message"] == "large")["extra"]["truncated"]
    await writer.close()


async def test_broken_extra_is_dropped_without_breaking_logger(db):
    class BrokenString:
        def __str__(self):
            raise RuntimeError("broken str")

    writer = DatabaseLogWriter(db)
    writer.enqueue(logging.makeLogRecord({"msg": "x", "bad": BrokenString()}))
    assert writer.dropped_count == 1
    assert writer._queue.empty()
    await writer.close()


def test_optional_sink_exception_never_propagates():
    def fail(record):
        raise RuntimeError("sink unavailable")

    with log_context(log_sink=fail):
        assert CorrelationFilter().filter(logging.makeLogRecord({"msg": "still succeeds"}))


async def test_close_waits_for_inflight_batch_and_flushes_remainder(db, monkeypatch):
    writer = DatabaseLogWriter(db)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = ApplicationLogRepository.add_batch

    async def delayed(repository, records):
        entered.set()
        await release.wait()
        await original(repository, records)

    monkeypatch.setattr(ApplicationLogRepository, "add_batch", delayed)
    writer.enqueue(logging.makeLogRecord({"msg": "first"}))
    writer.start()
    await asyncio.wait_for(entered.wait(), timeout=2)
    writer.enqueue(logging.makeLogRecord({"msg": "last"}))
    closing = asyncio.create_task(writer.close())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await asyncio.wait_for(closing, timeout=2)
    async with db.session() as session:
        _, total = await ApplicationLogRepository(session).list_logs()
    assert total == 2
    assert writer._task.done() and writer._queue.empty()
    assert writer.dropped_count == writer.write_failures == 0


async def test_cancelled_flush_counts_dequeued_records(db, monkeypatch):
    writer = DatabaseLogWriter(db)
    entered = asyncio.Event()

    async def blocking(repository, records):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ApplicationLogRepository, "add_batch", blocking)
    writer.enqueue(logging.makeLogRecord({"msg": "pending"}))
    pending = asyncio.create_task(writer.flush())
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert writer.dropped_count == writer.write_failures == 1
    await writer.close()
