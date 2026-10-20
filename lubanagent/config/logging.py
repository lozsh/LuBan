"""日志配置：stdlib 实现，console / json 两种格式，无第三方依赖。"""

import json
import logging
from datetime import UTC, datetime
from typing import Literal

# LogRecord 标准属性，之外的视为 extra 并入 JSON
_RESERVED_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)


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
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(level.upper())
