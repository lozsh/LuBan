"""Mock 与调试入口: 罐头结果/错误、调用记录、debug_tool 默认管线。"""

from typing import Any

import pytest

from lubanagent.core import ErrorCode, ToolDescriptor
from lubanagent.tool import MockToolHandler, debug_tool, make_mock_registered


def _descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        key="mock.echo",
        name="echo",
        args_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )


async def test_mock_handler_records_calls_and_returns_result() -> None:
    handler = MockToolHandler(result={"ok": True})
    assert await handler(text="a") == {"ok": True}
    assert await handler(text="b") == {"ok": True}
    assert handler.calls == [{"text": "a"}, {"text": "b"}]


async def test_mock_handler_raises_canned_error() -> None:
    handler = MockToolHandler(error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await handler(text="a")
    assert handler.calls == [{"text": "a"}]


async def test_make_mock_registered_runs_through_debug_tool() -> None:
    registered = make_mock_registered(_descriptor(), result="canned")
    result = await debug_tool(registered, {"text": "hi"})
    assert result.success is True
    assert result.value == "canned"
    assert registered.handler.calls == [{"text": "hi"}]


async def test_debug_tool_applies_validation_pipeline() -> None:
    registered = make_mock_registered(_descriptor(), result="canned")
    result = await debug_tool(registered, {})
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR.value


async def test_debug_tool_surfaces_mock_error_as_result() -> None:
    registered = make_mock_registered(_descriptor(), error=KeyError("k"))
    result = await debug_tool(registered, {"text": "hi"})
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_EXECUTION_FAILED.value
    assert result.error["details"]["exception"] == "KeyError"


def test_mock_handler_default_result_is_none() -> None:
    handler = MockToolHandler()
    assert handler.result is None
    assert handler.error is None
    assert handler.calls == []


async def test_mock_handler_accepts_arbitrary_kwargs() -> None:
    handler = MockToolHandler(result=1)
    await handler(**{"a": 1, "b": [1, 2], "c": {"d": True}})
    snapshot: dict[str, Any] = handler.calls[0]
    assert snapshot == {"a": 1, "b": [1, 2], "c": {"d": True}}
