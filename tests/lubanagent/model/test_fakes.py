"""FakeLLM: script 三态、bind_tools、确定性、录制重放闭环、流式。"""

from pathlib import Path

import pytest

from lubanagent.core import ErrorCode, LubanError
from lubanagent.model import (
    RecordingChatModel,
    ScriptedFakeChatModel,
    create_chat_model,
    create_fake,
    fake_definition,
    replay_script,
)


def test_script_text_reply() -> None:
    model = ScriptedFakeChatModel(script=["你好", "世界"])
    assert model.invoke("a").content == "你好"
    assert model.invoke("b").content == "世界"


def test_script_tool_calls_reply() -> None:
    model = ScriptedFakeChatModel(
        script=[{"tool_calls": [{"name": "search", "args": {"q": "x"}, "id": "call-1"}]}]
    )
    message = model.invoke("a")
    assert message.tool_calls == [
        {"name": "search", "args": {"q": "x"}, "id": "call-1", "type": "tool_call"}
    ]


def test_script_exception_item_raises() -> None:
    model = ScriptedFakeChatModel(script=[ValueError("boom")])
    with pytest.raises(ValueError, match="boom"):
        model.invoke("a")


def test_script_exhausted_raises_internal_error() -> None:
    model = ScriptedFakeChatModel(script=["only-once"])
    model.invoke("a")
    with pytest.raises(LubanError) as exc_info:
        model.invoke("b")
    assert exc_info.value.code == ErrorCode.INTERNAL_ERROR
    assert exc_info.value.details == {"reason": "script exhausted"}


def test_deterministic_replay() -> None:
    script = ["固定答案", {"tool_calls": [{"name": "t", "args": {}, "id": "c1"}]}]
    first = [ScriptedFakeChatModel(script=list(script)).invoke("x").content]
    second = [ScriptedFakeChatModel(script=list(script)).invoke("x").content]
    assert first == second == ["固定答案"]


def test_bind_tools_returns_clone_recording_tools() -> None:
    model = ScriptedFakeChatModel(script=["ok"])
    bound = model.bind_tools([{"name": "search"}])
    assert bound is not model
    assert bound.bound_tools == [{"name": "search"}]
    assert model.bound_tools == []
    message = bound.invoke("x")
    assert message.content == "ok"


def test_llm_type() -> None:
    assert ScriptedFakeChatModel(script=[])._llm_type == "scripted-fake"


def test_fake_definition_defaults() -> None:
    definition = fake_definition(script=["ok"])
    assert definition.factory == "fake"
    assert definition.provider == "fake"
    assert definition.params == {"script": ["ok"]}
    capability = definition.capability
    assert all(
        getattr(capability, name)
        for name in (
            "chat", "streaming", "tool_calling", "structured_output", "vision", "reasoning"
        )
    )


def test_create_fake_echoes_capability() -> None:
    definition = fake_definition(script=["ok"])
    model = create_fake(definition)
    assert model.capability_echo["tool_calling"] is True
    assert model.invoke("x").content == "ok"


def test_create_chat_model_with_fake_factory() -> None:
    model = create_chat_model(fake_definition(script=["ok"]))
    assert isinstance(model, ScriptedFakeChatModel)
    assert model.invoke("x").content == "ok"


async def test_astream_produces_non_empty_chunks() -> None:
    model = ScriptedFakeChatModel(script=["hello world again"])
    chunks = [chunk async for chunk in model.astream("x")]
    assert chunks
    assert "".join(str(chunk.content) for chunk in chunks) == "hello world again"


def test_stream_produces_chunks() -> None:
    model = ScriptedFakeChatModel(script=["a b"])
    chunks = list(model.stream("x"))
    assert "".join(str(chunk.content) for chunk in chunks) == "a b"


def test_record_and_replay_round_trip(tmp_path: Path) -> None:
    record_path = tmp_path / "record.jsonl"
    source = ScriptedFakeChatModel(
        script=["你好", {"tool_calls": [{"name": "search", "args": {"q": "x"}, "id": "c1"}]}]
    )
    recorder = RecordingChatModel(wrapped=source, record_path=str(record_path))
    assert recorder.invoke("a").content == "你好"
    assert recorder.invoke("b").tool_calls[0]["name"] == "search"

    script = replay_script(record_path)
    replayed = create_fake(fake_definition(script=script))
    assert replayed.invoke("a").content == "你好"
    tool_calls = replayed.invoke("b").tool_calls
    assert tool_calls[0]["name"] == "search"
    assert tool_calls[0]["args"] == {"q": "x"}


def test_record_error_entry(tmp_path: Path) -> None:
    record_path = tmp_path / "record.jsonl"
    source = ScriptedFakeChatModel(script=[ValueError("boom")])
    recorder = RecordingChatModel(wrapped=source, record_path=str(record_path))
    with pytest.raises(ValueError, match="boom"):
        recorder.invoke("a")
    script = replay_script(record_path)
    assert len(script) == 1
    assert isinstance(script[0], Exception)
