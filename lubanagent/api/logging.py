"""纯 ASGI 请求日志：覆盖 SSE 全生命周期，不使用会切断上下文的 BaseHTTPMiddleware。"""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lubanagent.core.log_context import log_context
from lubanagent.observability.logging import DatabaseLogWriter

logger = logging.getLogger("lubanagent.api.requests")
_IDENTIFIER = re.compile(r"[a-zA-Z0-9._-]{1,128}\Z")


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp, *, writer: DatabaseLogWriter | None = None) -> None:
        self.app = app
        self.writer = writer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers", []))
        try:
            candidate = incoming.get(b"x-request-id", b"").decode("ascii")
        except UnicodeDecodeError:
            candidate = ""
        request_id = candidate if _IDENTIFIER.fullmatch(candidate) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        # Scope state survives exception handling outside this middleware and is returned
        # with errors. No authorization/query/body values are ever logged here.
        started = time.perf_counter()
        status = 500

        async def traced_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with log_context(
            request_id=request_id,
            session_id=None,
            run_id=None,
            trace_id=None,
            parent_run_id=None,
            root_run_id=None,
            subject=None,
            log_sink=self.writer.enqueue if self.writer else None,
        ):
            logger.info(
                "HTTP request started", extra={"method": scope["method"], "path": scope["path"]}
            )
            try:
                await self.app(scope, receive, traced_send)
            except Exception:
                logger.exception(
                    "HTTP request failed", extra={"method": scope["method"], "path": scope["path"]}
                )
                raise
            finally:
                principal = state.get("principal")
                with log_context(subject=getattr(principal, "subject", None)):
                    logger.info(
                        "HTTP request completed",
                        extra={
                            "method": scope["method"],
                            "path": scope["path"],
                            "status": status,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        },
                    )
                if self.writer is not None:
                    await self.writer.flush()
