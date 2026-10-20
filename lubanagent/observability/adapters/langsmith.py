"""LangSmith 适配器：SDK 懒 import + key 缺失自动降级为 noop，用 RunTree 翻译 core 事件。"""

import logging
from typing import Any

from lubanagent.core import Run, RunStatus, TraceEvent, TraceEventType
from lubanagent.extension.protocols import TraceHandle
from lubanagent.observability.adapters.noop import NoneHandle

logger = logging.getLogger(__name__)


class LangSmithHandle:
    """LangSmith RunTree 句柄：挂根 RunTree 与进行中的子 run。"""

    def __init__(self, *, run_id: str, root: Any) -> None:
        self.run_id = run_id
        self.root = root
        self.children: dict[int | None, Any] = {}


class LangSmithBackend:
    """把 core TraceEvent 翻译成 LangSmith RunTree；SDK 未安装或 key 缺失时等同 noop。"""

    def __init__(self, *, api_key: str | None, project: str | None = None) -> None:
        self._api_key = api_key
        self._project = project
        self._run_tree_cls: Any = None
        self._disabled = False

    @property
    def name(self) -> str:
        return "langsmith"

    def start_trace(self, run: Run) -> TraceHandle:
        run_tree_cls = self._ensure_run_tree()
        if run_tree_cls is None:
            return NoneHandle(run_id=run.id)
        root = run_tree_cls(
            name=run.agent_key,
            run_type="chain",
            inputs=run.input,
            project_name=self._project,
            extra={
                "metadata": {
                    "agent_version": run.agent_version,
                    "run_id": run.id,
                    "session_id": run.session_id,
                }
            },
        )
        root.post()
        return LangSmithHandle(run_id=run.id, root=root)

    def emit(self, handle: TraceHandle, event: TraceEvent) -> None:
        if self._disabled or not isinstance(handle, LangSmithHandle):
            return
        payload = event.payload
        if event.type is TraceEventType.MODEL_CALL_STARTED:
            child = handle.root.create_child(
                name=payload.get("model_key", "model_call"),
                run_type="llm",
                inputs={"input": payload.get("input")},
            )
            child.post()
            handle.children[event.step_index] = child
        elif event.type is TraceEventType.MODEL_CALL_COMPLETED:
            child = handle.children.pop(event.step_index, None)
            if child is None:
                child = handle.root.create_child(
                    name=payload.get("model_key", "model_call"), run_type="llm", inputs={}
                )
                child.post()
            # H10 事件 schema：usage 仅在 usage_known=True 时传数值，未知不冒充 0
            usage_known = bool(payload.get("usage_known"))
            extra: dict[str, Any] = {}
            if usage_known:
                extra["usage"] = {
                    "input_tokens": payload.get("input_tokens"),
                    "output_tokens": payload.get("output_tokens"),
                }
            else:
                extra["metadata"] = {"usage_unknown": True}
            child.end(
                outputs={"output": payload.get("text")},
                extra=extra,
            )
            child.patch()
        elif event.type is TraceEventType.TOOL_CALL_STARTED:
            # H10：适配器读 Runtime 事件真实键（tool/args），与 DB/SSE/UI 同一约定
            child = handle.root.create_child(
                name=payload.get("tool", "tool"),
                run_type="tool",
                inputs={"input": payload.get("args")},
            )
            child.post()
            handle.children[event.step_index] = child
        elif event.type in (
            TraceEventType.TOOL_CALL_COMPLETED,
            TraceEventType.TOOL_CALL_FAILED,
        ):
            child = handle.children.pop(event.step_index, None)
            if child is None:
                child = handle.root.create_child(
                    name=payload.get("tool", "tool"), run_type="tool", inputs={}
                )
                child.post()
            if event.type is TraceEventType.TOOL_CALL_COMPLETED:
                child.end(outputs={"output": payload.get("result")})
            else:
                child.end(error=str(payload.get("error")))
            child.patch()
        else:
            child = handle.root.create_child(
                name=event.type.value, run_type="chain", inputs=payload
            )
            child.end()
            child.post()

    def end_trace(
        self, handle: TraceHandle, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        if self._disabled or not isinstance(handle, LangSmithHandle):
            return
        summary = summary or {}
        if status is RunStatus.FAILED:
            handle.root.end(error=str(summary.get("error", "run failed")))
        else:
            handle.root.end(outputs={"status": status.value, **summary})
        handle.root.patch()

    # ---- 内部 ----
    def replay(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """回放历史失败事件：原 RunTree 已失（跨进程），按独立子 run 重发，run_id 进 metadata。"""
        run_tree_cls = self._ensure_run_tree()
        if run_tree_cls is None:
            raise RuntimeError("langsmith 已降级为 noop（key 缺失或 SDK 未安装），无法回放")
        child = run_tree_cls(
            name=event_type,
            run_type="chain",
            inputs=payload,
            project_name=self._project,
            extra={"metadata": {"run_id": run_id, "replayed": True}},
        )
        child.end()
        child.post()

    def _ensure_run_tree(self) -> Any:
        if self._disabled:
            return None
        if self._run_tree_cls is not None:
            return self._run_tree_cls
        if not self._api_key:
            logger.warning("langsmith api key 缺失，观测外发降级为 noop（内部 DB 不受影响）")
            self._disabled = True
            return None
        try:
            import langsmith
        except ImportError:
            logger.warning("langsmith SDK 未安装，观测外发降级为 noop（内部 DB 不受影响）")
            self._disabled = True
            return None
        self._run_tree_cls = langsmith.RunTree
        return self._run_tree_cls
