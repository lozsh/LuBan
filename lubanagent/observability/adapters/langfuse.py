"""Langfuse 适配器：SDK 懒 import + key 缺失自动降级为 noop（架构 §6.5），只翻译 core 事件。"""

import logging
from typing import Any

from lubanagent.core import Run, RunStatus, TraceEvent, TraceEventType
from lubanagent.extension.protocols import TraceHandle
from lubanagent.observability.adapters.noop import NoneHandle

logger = logging.getLogger(__name__)


class LangfuseHandle:
    """Langfuse trace 句柄：挂 trace 对象与进行中的 generation/span。"""

    def __init__(self, *, run_id: str, client: Any, trace: Any) -> None:
        self.run_id = run_id
        self.client = client
        self.trace = trace
        self.generations: dict[int | None, Any] = {}
        self.spans: dict[int | None, Any] = {}


class LangfuseBackend:
    """把 core TraceEvent 翻译成 Langfuse 调用；SDK 未安装或 key 缺失时等同 noop。"""

    def __init__(
        self, *, host: str | None, public_key: str | None, secret_key: str | None
    ) -> None:
        self._host = host
        self._public_key = public_key
        self._secret_key = secret_key
        self._client: Any = None
        self._disabled = False

    @property
    def name(self) -> str:
        return "langfuse"

    def start_trace(self, run: Run) -> TraceHandle:
        client = self._ensure_client()
        if client is None:
            return NoneHandle(run_id=run.id)
        trace = client.trace(
            name=run.agent_key,
            metadata={
                "agent_version": run.agent_version,
                "run_id": run.id,
                "session_id": run.session_id,
            },
        )
        return LangfuseHandle(run_id=run.id, client=client, trace=trace)

    def emit(self, handle: TraceHandle, event: TraceEvent) -> None:
        if self._disabled or not isinstance(handle, LangfuseHandle):
            return
        payload = event.payload
        meta = {"run_id": event.run_id, "step_index": event.step_index}
        if event.type is TraceEventType.MODEL_CALL_STARTED:
            generation = handle.trace.generation(
                name="model_call",
                model=payload.get("model_key"),
                input=payload.get("input"),
                metadata=meta,
            )
            handle.generations[event.step_index] = generation
        elif event.type is TraceEventType.MODEL_CALL_COMPLETED:
            # H10 事件 schema：usage 仅在 usage_known=True 时传数值，未知不冒充 0
            usage_known = bool(payload.get("usage_known"))
            usage = (
                {
                    "input": payload.get("input_tokens"),
                    "output": payload.get("output_tokens"),
                }
                if usage_known
                else None
            )
            if not usage_known:
                meta = {**meta, "usage_unknown": True}
            generation = handle.generations.pop(event.step_index, None)
            if generation is None:
                generation = handle.trace.generation(
                    name="model_call", model=payload.get("model_key"), metadata=meta
                )
            generation.end(output=payload.get("text"), usage=usage, metadata=meta)
        elif event.type is TraceEventType.TOOL_CALL_STARTED:
            # H10：适配器读 Runtime 事件真实键（tool/args），与 DB/SSE/UI 同一约定
            span = handle.trace.span(
                name=payload.get("tool", "tool"),
                input=payload.get("args"),
                metadata=meta,
            )
            handle.spans[event.step_index] = span
        elif event.type in (
            TraceEventType.TOOL_CALL_COMPLETED,
            TraceEventType.TOOL_CALL_FAILED,
        ):
            span = handle.spans.pop(event.step_index, None)
            if span is None:
                span = handle.trace.span(
                    name=payload.get("tool", "tool"), metadata=meta
                )
            if event.type is TraceEventType.TOOL_CALL_COMPLETED:
                span.end(output=payload.get("result"))
            else:
                span.end(output=payload.get("error"), level="ERROR")
        else:
            handle.client.event(name=event.type.value, input=payload, metadata=meta)

    def end_trace(
        self, handle: TraceHandle, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        if self._disabled or not isinstance(handle, LangfuseHandle):
            return
        summary = summary or {}
        handle.trace.update(
            status=status.value,
            end_reason=summary.get("end_reason"),
            usage=summary.get("token_usage"),
            cost=summary.get("cost"),
        )
        handle.client.flush()

    # ---- 内部 ----
    def replay(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """回放历史失败事件：原句柄已失（跨进程），按通用 event 重发，run_id 进 metadata。"""
        client = self._ensure_client()
        if client is None:
            raise RuntimeError("langfuse 已降级为 noop（key 缺失或 SDK 未安装），无法回放")
        client.event(
            name=event_type,
            input=payload,
            metadata={"run_id": run_id, "replayed": True},
        )

    def _ensure_client(self) -> Any:
        if self._disabled:
            return None
        if self._client is not None:
            return self._client
        if not (self._public_key and self._secret_key):
            logger.warning("langfuse key 缺失，观测外发降级为 noop（内部 DB 不受影响）")
            self._disabled = True
            return None
        try:
            import langfuse
        except ImportError:
            logger.warning("langfuse SDK 未安装，观测外发降级为 noop（内部 DB 不受影响）")
            self._disabled = True
            return None
        self._client = langfuse.Langfuse(
            host=self._host,
            public_key=self._public_key,
            secret_key=self._secret_key,
        )
        return self._client
