"""avatars 路由：Agent 虚拟形象（上传/获取/移除，展示用 SVG/PNG/GLB/GLTF）。

AvatarManager 由工厂挂 app.state.avatar_manager；路由经 request.app.state 取用
文件存取逻辑（同 knowledge 先例——管理器不进路由层 import）。本文件仅静态
import avatar 模块的常量 AVATAR_MEDIA_TYPES（纯数据，无逻辑依赖）。
上传即版本：服务端复制当前定义设 avatar 字段、追加新版本（照 Prompt 分配先例），
单一原子操作，前端不做"传文件 + 存定义"两步。
"""

from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lubanagent.api.deps import CtxDep
from lubanagent.avatar.manager import AVATAR_MEDIA_TYPES, MAX_AVATAR_BYTES
from lubanagent.core import AgentDefinition, ErrorCode, LubanError
from lubanagent.persistence import AgentRepository

router = APIRouter(tags=["avatars"])

_AvatarFile = Annotated[UploadFile, File(description="形象文件，支持 svg/png/glb/gltf")]
_UPLOAD_CHUNK_BYTES = 64 * 1024

# 用户上传的 SVG 直接导航打开也不执行脚本（img 内嵌本就不执行；CSP 兜底直开场景）
_AVATAR_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}


class AvatarRemovedOut(BaseModel):
    removed: bool


def _manager(request: Request):  # type: ignore[no-untyped-def]
    """读 app.state.avatar_manager（工厂装配）；未装配属装配错误。"""
    manager = getattr(request.app.state, "avatar_manager", None)
    if manager is None:
        raise LubanError(ErrorCode.CONFIG_ERROR, "AvatarManager 未装配")
    return manager


async def _current_definition(key: str, ctx: CtxDep) -> AgentDefinition:
    async with ctx.db.session() as session:
        definition = await AgentRepository(session).get_agent(key)
    if definition is None:
        raise LubanError(
            ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {key}", details={"agent_key": key}
        )
    return definition


async def _save_version(key: str, definition: AgentDefinition, ctx: CtxDep) -> AgentDefinition:
    """复制当前定义设 avatar 后追加新版本（版本号 +1，内容不可变快照）。"""
    definition = definition.model_copy(update={"version": definition.version + 1})
    async with ctx.db.session() as session:
        repo = AgentRepository(session)
        await repo.create_version(key, definition)
    return definition


@router.put("/agents/{key}/avatar", response_model=AgentDefinition)
async def upload_avatar(
    key: str, file: _AvatarFile, request: Request, ctx: CtxDep
) -> AgentDefinition:
    definition = await _current_definition(key, ctx)
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_AVATAR_BYTES:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"形象文件超过上限 {MAX_AVATAR_BYTES} 字节",
                details={
                    "filename": file.filename or "untitled",
                    "size": size,
                    "max": MAX_AVATAR_BYTES,
                },
            )
        chunks.append(chunk)
    stored = await _manager(request).save(key, file.filename or "untitled", b"".join(chunks))
    return await _save_version(key, definition.model_copy(update={"avatar": stored}), ctx)


@router.get("/agents/{key}/avatar")
async def get_avatar(key: str, request: Request, ctx: CtxDep) -> FileResponse:
    definition = await _current_definition(key, ctx)
    if not definition.avatar:
        raise LubanError(
            ErrorCode.AVATAR_NOT_FOUND, f"Agent 未设置形象: {key}", details={"agent_key": key}
        )
    try:
        path = await _manager(request).get_path(key, definition.avatar)
    except LubanError as exc:
        # 文件缺失（定义引用了不存在的文件）对外同为资源不存在
        if exc.code != ErrorCode.VALIDATION_ERROR:
            raise
        raise LubanError(
            ErrorCode.AVATAR_NOT_FOUND,
            f"Agent 形象文件不存在: {key}",
            details={"agent_key": key, "filename": definition.avatar},
        ) from None
    suffix = path.suffix.lower()
    return FileResponse(
        path,
        media_type=AVATAR_MEDIA_TYPES.get(suffix, "application/octet-stream"),
        headers=_AVATAR_SECURITY_HEADERS,
    )


@router.delete("/agents/{key}/avatar", response_model=AvatarRemovedOut)
async def delete_avatar(key: str, request: Request, ctx: CtxDep) -> AvatarRemovedOut:
    definition = await _current_definition(key, ctx)
    if not definition.avatar:
        raise LubanError(
            ErrorCode.AVATAR_NOT_FOUND, f"Agent 未设置形象: {key}", details={"agent_key": key}
        )
    await _manager(request).delete(key)
    await _save_version(key, definition.model_copy(update={"avatar": None}), ctx)
    return AvatarRemovedOut(removed=True)
