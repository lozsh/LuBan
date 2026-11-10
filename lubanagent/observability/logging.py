"""上下文关联的程序日志落库；有界批量队列，不在 logger 调用中等待数据库。"""

import asyncio
import json
import logging
import math
import queue
import re
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from lubanagent.core.log_context import CORRELATION_FIELDS, log_context
from lubanagent.core.redact import is_secret_key, redact_value
from lubanagent.persistence import Database
from lubanagent.persistence.repositories.log import ApplicationLogRepository

_STANDARD = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_CREDENTIALS = re.compile(
    r"(?i)(?:bearer\s+\S+|sk-[a-z0-9_-]{8,}|"
    r"(?:password|passwd|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s,;]+)"
)
_MAX_TEXT = 32768
_MAX_EXTRA = 16384


def _bounded_text(value: str, limit: int = _MAX_TEXT) -> str:
    # MySQL TEXT is limited in bytes, not Python characters (Chinese is UTF-8).
    return value.encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def sanitize_log_text(value: str) -> str:
    """日志文本凭证形态隐藏；并不保证识别任意业务 PII。"""
    try:
        value = str(redact_value(value))
    except (ValueError, TypeError):
        # Malformed credential URLs must not break application logging.
        value = "[REDACTED]"
    return _bounded_text(_CREDENTIALS.sub("[REDACTED]", value))


class DatabaseLogWriter:
    """每个应用独立的 sink；ContextVar 路由日志，避免多个 app/并行请求串库。"""

    def __init__(self, db: Database, *, capacity: int = 10000, retention_days: int = 30) -> None:
        self.db = db
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=capacity)
        self._lock = asyncio.Lock()
        self._counter_lock = threading.Lock()
        self.dropped_count = 0
        self.write_failures = 0
        self._task: asyncio.Task[None] | None = None
        self._retention_days = retention_days
        # None means never cleaned; monotonic() may start below one hour on CI
        # or even at zero. A numeric sentinel must not delay the first cleanup.
        self._last_cleanup: float | None = None
        self._closed = False
        self._stop = asyncio.Event()

    def enqueue(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        if str(record.name).startswith(("sqlalchemy.engine", "aiosqlite")):
            # DEBUG SQL bind values can contain passwords/tokens; do not persist
            # raw database statements/parameters as application audit logs.
            return
        try:
            self._enqueue(record)
        except Exception:
            # A cyclic/non-serializable extra or broken __str__ is a log failure,
            # never a failure of the business operation that emitted it.
            self._record_drop(1)

    def _record_drop(self, count: int, *, write_failure: bool = False) -> None:
        with self._counter_lock:
            self.dropped_count += count
            if write_failure:
                self.write_failures += 1

    def _enqueue(self, record: logging.LogRecord) -> None:
        exception = None
        if record.exc_info:
            exception = logging.Formatter().formatException(record.exc_info)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD and key not in CORRELATION_FIELDS
        }
        # Convert arbitrary extra values before queueing; never retain traceback objects,
        # mutable user arguments or request bodies in the async queue.
        extras = self._sanitize_extra(extras)
        serialized = json.dumps(extras, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > _MAX_EXTRA:
            extras = {"truncated": True, "preview": _bounded_text(serialized, 4096)}
        payload: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.fromtimestamp(record.created, UTC),
            "level": str(record.levelname)[:16],
            "logger": str(record.name)[:255],
            "message": sanitize_log_text(record.getMessage()),
            "exception": sanitize_log_text(exception) if exception else None,
            "pathname": record.pathname[:1024],
            "lineno": record.lineno,
            "extra": extras,
        }
        for field in CORRELATION_FIELDS:
            value = getattr(record, field, None)
            payload[field] = (
                str(value)[: 128 if field not in ("run_id", "root_run_id", "parent_run_id") else 64]
                if value
                else None
            )
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._record_drop(1)

    @classmethod
    def _sanitize_extra(cls, value: Any, *, depth: int = 0) -> Any:
        if depth >= 8:
            return "[TRUNCATED]"
        if isinstance(value, dict):
            result = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 100:
                    result["__truncated__"] = True
                    break
                raw_key = str(key)
                key = _bounded_text(raw_key, 256)
                result[key] = (
                    "[REDACTED]"
                    if is_secret_key(raw_key)
                    else cls._sanitize_extra(item, depth=depth + 1)
                )
            return result
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_extra(item, depth=depth + 1) for item in value[:100]]
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        return sanitize_log_text(str(value))

    def start(self) -> None:
        if not self._closed and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run(), name="luban-database-logs")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.25)
            except TimeoutError:
                await self.flush()

    async def flush(self) -> None:
        """查询与正常关闭前刷新；写入失败不递归 logger，也不阻断业务。"""
        async with self._lock:
            # Snapshot the queue length: logging during a flush cannot starve this call.
            remaining = self._queue.qsize()
            while remaining:
                batch = []
                for _ in range(min(remaining, 200)):
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                remaining -= len(batch)
                if not batch:
                    break
                try:
                    with log_context(persist_logs=False):
                        async with self.db.session() as session:
                            await ApplicationLogRepository(session).add_batch(batch)
                except Exception as exc:
                    self._record_drop(len(batch), write_failure=True)
                    # Explicit failure signal, without credentials or recursive logging.
                    try:
                        sys.stderr.write(f"Luban log persistence failed: {type(exc).__name__}\n")
                    except (OSError, ValueError):
                        pass
                except asyncio.CancelledError:
                    # Cancellation after dequeue is an ambiguous DB write; do not
                    # silently pretend that the entire batch was durably stored.
                    self._record_drop(len(batch), write_failure=True)
                    raise
            if self._last_cleanup is None or time.monotonic() - self._last_cleanup >= 3600:
                try:
                    with log_context(persist_logs=False):
                        async with self.db.session() as session:
                            await ApplicationLogRepository(session).delete_before(
                                datetime.now(UTC) - timedelta(days=self._retention_days)
                            )
                    self._last_cleanup = time.monotonic()
                except Exception:
                    # Do not hide ingest health: cleanup failure is visible separately.
                    self._record_drop(0, write_failure=True)

    async def close(self) -> None:
        # Stop accepting first, then wait for the in-flight batch instead of
        # cancelling it halfway through commit. Finally flush the remaining queue.
        self._closed = True
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.flush()
