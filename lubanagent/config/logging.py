"""日志配置：stdlib 实现，console / json 两种格式，无第三方依赖。"""

import json
import logging
from datetime import UTC, datetime
from typing import Literal

from lubanagent.core.log_context import CORRELATION_FIELDS, current_log_context, log_sink

# LogRecord 标准属性，之外的视为 extra 并入 JSON
_RESERVED_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)


class CorrelationFilter(logging.Filter):
    """在记录发出时绑定可信执行上下文，而不是在后台入库时猜测关联 ID。"""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current_log_context()
        for field in CORRELATION_FIELDS:
            if field in context:
                setattr(record, field, context[field])
        sink = log_sink()
        if sink is not None:
            try:
                sink(record)
            except Exception:
                # Optional persistence must never turn a logger call into a
                # business exception or recursively emit another log record.
                pass
        return True


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        identifiers = " ".join(
            f"{name}={getattr(record, name)}"
            for name in ("request_id", "session_id", "run_id", "trace_id")
            if getattr(record, name, None)
        )
        return f"{rendered} [{identifiers}]" if identifiers else rendered


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志格式。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", fmt: Literal["console", "json"] = "console") -> None:
    """配置 root logger；重复调用先清已有 handler，不叠加。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    handler = logging.StreamHandler()
    handler.addFilter(CorrelationFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ConsoleFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
