"""日志配置契约测试：JSON 格式、extra 并入、重复调用不叠加 handler。"""

import json
import logging

import pytest
from _pytest.capture import CaptureFixture

from lubanagent.config import configure_logging


@pytest.fixture(autouse=True)
def reset_logging():
    yield
    # 还原 root logger，避免污染其他测试
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


def test_json_format_output(capsys: CaptureFixture) -> None:
    configure_logging(level="INFO", fmt="json")
    logging.getLogger("test.json").info("你好")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["message"] == "你好"
    assert payload["logger"] == "test.json"
    assert "time" in payload


def test_json_format_merges_extra(capsys: CaptureFixture) -> None:
    configure_logging(level="INFO", fmt="json")
    logging.getLogger("test.extra").info("with extra", extra={"run_id": "r-1"})
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["run_id"] == "r-1"


def test_console_format_output(capsys: CaptureFixture) -> None:
    configure_logging(level="DEBUG", fmt="console")
    logging.getLogger("test.console").debug("plain")
    out = capsys.readouterr().err
    assert "DEBUG" in out and "plain" in out and "test.console" in out


def test_reconfigure_does_not_stack_handlers() -> None:
    configure_logging()
    configure_logging(fmt="json")
    configure_logging()
    assert len(logging.getLogger().handlers) == 1
