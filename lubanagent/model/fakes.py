"""测试 FakeLLM 与录制重放: 单测与 CI 零网络零密钥的基建。"""

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, PrivateAttr

from lubanagent.core import ErrorCode, LubanError, ModelCapability, ModelDefinition


class ScriptedFakeChatModel(BaseChatModel):
    """按 script 顺序回放确定性响应。

    script 条目三态: str → 文本回复; dict {"tool_calls": [{"name", "args", "id"}]} → 工具调用;
    Exception 实例 → 调用时抛出。
    """

    script: list[Any]
    capability_echo: dict[str, Any] = Field(default_factory=dict)  # 仅记录用, 能力声明在定义层
    bound_tools: list[Any] = Field(default_factory=list)

    _cursor: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "ScriptedFakeChatModel":
        clone = self.model_copy()
        clone.bound_tools = list(tools)
        return clone

    def _next(self) -> Any:
        if self._cursor >= len(self.script):
            raise LubanError(
                ErrorCode.INTERNAL_ERROR,
                "fake script 已耗尽",
                details={"reason": "script exhausted"},
            )
        item = self.script[self._cursor]
        self._cursor += 1
        if isinstance(item, Exception):
            raise item
        return item

    @staticmethod
    def _to_message(item: Any) -> AIMessage:
        if isinstance(item, str):
            return AIMessage(content=item)
        if isinstance(item, dict) and "tool_calls" in item:
            calls = [
                {
                    "name": call["name"],
                    "args": call.get("args", {}),
                    "id": call.get("id") or f"call_{index}",
                    "type": "tool_call",
                }
                for index, call in enumerate(item["tool_calls"])
            ]
            return AIMessage(content="", tool_calls=calls)
        raise LubanError(
            ErrorCode.INTERNAL_ERROR,
            f"无法识别的 script 条目: {item!r}",
            details={"reason": "invalid script item"},
        )

    def _result(self) -> ChatResult:
        message = self._to_message(self._next())
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._result()

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._result()

    @staticmethod
    def _chunks_for(message: AIMessage) -> Iterator[ChatGenerationChunk]:
        if message.tool_calls:
            for index, call in enumerate(message.tool_calls):
                chunk = AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"], ensure_ascii=False),
                            "id": call["id"],
                            "index": index,
                        }
                    ],
                )
                yield ChatGenerationChunk(message=chunk)
            return
        for position, word in enumerate(str(message.content).split(" ")):
            text = word if position == 0 else f" {word}"
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from self._chunks_for(self._to_message(self._next()))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._chunks_for(self._to_message(self._next())):
            yield chunk


class RecordingChatModel(BaseChatModel):
    """包装任意 BaseChatModel, 调用结果追加写 JSONL, 供录制重放闭环。"""

    wrapped: BaseChatModel
    record_path: str

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _append(self, record: dict[str, Any]) -> None:
        path = Path(self.record_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _record_for(message: AIMessage) -> dict[str, Any]:
        if message.tool_calls:
            return {
                "type": "tool_calls",
                "tool_calls": [
                    {"name": call["name"], "args": call["args"], "id": call["id"]}
                    for call in message.tool_calls
                ],
            }
        return {"type": "text", "content": str(message.content)}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            result = self.wrapped._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as exc:
            self._append({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
            raise
        message = result.generations[0].message
        self._append(self._record_for(message))
        return result

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            result = await self.wrapped._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        except Exception as exc:
            self._append({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
            raise
        message = result.generations[0].message
        self._append(self._record_for(message))
        return result


def replay_script(path: str | Path) -> list[Any]:
    """读 JSONL 还原为 script 列表 (录制/重放闭环) 。"""
    script: list[Any] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record_type = record.get("type")
        if record_type == "text":
            script.append(record["content"])
        elif record_type == "tool_calls":
            script.append({"tool_calls": record["tool_calls"]})
        elif record_type == "error":
            script.append(RuntimeError(record["error"]))
        else:
            raise LubanError(
                ErrorCode.INTERNAL_ERROR,
                f"未知录制条目类型: {record_type}",
                details={"record": record},
            )
    return script


def fake_definition(
    *,
    key: str = "fake-chat",
    script: list[Any] | None = None,
    capability: ModelCapability | None = None,
) -> ModelDefinition:
    """factory="fake" 的 ModelDefinition; script 放 params["script"], capability 缺省全 True。"""
    if capability is None:
        capability = ModelCapability(
            chat=True,
            streaming=True,
            tool_calling=True,
            structured_output=True,
            vision=True,
            reasoning=True,
        )
    return ModelDefinition(
        key=key,
        provider="fake",
        model_name="scripted-fake",
        factory="fake",
        capability=capability,
        params={"script": script if script is not None else []},
    )


def create_fake(definition: ModelDefinition) -> ScriptedFakeChatModel:
    """从 definition.params["script"] 构造; 注册进 default_model_factory (name="fake")。"""
    return ScriptedFakeChatModel(
        script=list(definition.params.get("script", [])),
        capability_echo=dict(definition.capability.model_dump()),
    )
