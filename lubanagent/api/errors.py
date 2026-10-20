"""错误映射：LubanError/校验错误/未捕获异常 → 统一 ErrorOut + HTTP 状态码。"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lubanagent.api.schemas import ErrorOut
from lubanagent.core import ErrorCode, LubanError

logger = logging.getLogger(__name__)

# 错误码 → HTTP 状态；未覆盖码默认 500
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.AGENT_NOT_FOUND: 404,
    ErrorCode.AGENT_VERSION_NOT_FOUND: 404,
    ErrorCode.RUN_NOT_FOUND: 404,
    ErrorCode.MODEL_NOT_FOUND: 404,
    ErrorCode.TOOL_NOT_FOUND: 404,
    ErrorCode.PROMPT_NOT_FOUND: 404,
    ErrorCode.PROMPT_VERSION_NOT_FOUND: 404,
    ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND: 404,
    ErrorCode.GRAPH_NOT_FOUND: 404,
    ErrorCode.AVATAR_NOT_FOUND: 404,
    ErrorCode.EVAL_RESULT_NOT_FOUND: 404,
    ErrorCode.AUTH_FAILED: 401,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.TOOL_PERMISSION_DENIED: 403,
    ErrorCode.AGENT_VERSION_UNRUNNABLE: 422,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.CAPABILITY_MISMATCH: 422,
    ErrorCode.NO_FALLBACK_AVAILABLE: 422,
    ErrorCode.BRAIN_NOT_MAPPED: 422,
    ErrorCode.GRAPH_STRUCTURE_UNAVAILABLE: 422,
    ErrorCode.CONFIG_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}


def conflict_response(message: str, details: dict | None = None) -> JSONResponse:
    """409 + ErrorOut(VALIDATION_ERROR)：key/版本重复等冲突的路由级状态覆盖。"""
    body = ErrorOut.of(ErrorCode.VALIDATION_ERROR.value, message, details=details)
    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))


def register_error_handlers(app: FastAPI) -> None:
    """注册顺序：RequestValidationError → LubanError → Exception。"""

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = ErrorOut.of(
            ErrorCode.VALIDATION_ERROR.value,
            "请求参数校验失败",
            details={
                "errors": [
                    {"loc": list(error["loc"]), "type": error["type"], "msg": error["msg"]}
                    for error in exc.errors()
                ]
            },
        )
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @app.exception_handler(LubanError)
    async def _on_luban_error(request: Request, exc: LubanError) -> JSONResponse:
        status = ERROR_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(status_code=status, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def _on_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 堆栈只进日志，不进响应
        logger.exception("未捕获异常: %s %s", request.method, request.url.path)
        body = ErrorOut.of(ErrorCode.INTERNAL_ERROR.value, "服务器内部错误")
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))
