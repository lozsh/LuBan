"""skills 路由：目录装载 + zip 上传安装 + 删除，经 app.state.skill_registry 内存视图。

SkillRegistry 由用户领地（app/main.py）挂 app.state.skill_registry；路由经
request.app.state 取用，不 import skill 模块（api → skill 为分层禁忌）。
list 读内存视图（scan 后常驻），不依赖 DB；上传 zip 解压到 skills_root/{name}/
后 scan 刷新；删除移除目录后 scan 刷新。source of truth 是文件系统。
"""

from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from lubanagent.api.schemas import SkillOut
from lubanagent.core import ErrorCode, LubanError, SkillPackage

router = APIRouter(tags=["skills"])

_MAX_SKILL_ZIP_BYTES = 10 * 1024 * 1024  # 10MB
_UPLOAD_CHUNK_BYTES = 64 * 1024
SkillZip = Annotated[UploadFile, File(description="Skill zip 包，含 SKILL.md")]


def _to_out(package: SkillPackage) -> SkillOut:
    return SkillOut(
        key=package.meta.name,
        name=package.meta.name,
        version=package.meta.version,
        manifest=package.model_dump(mode="json"),
    )


def _registry(request: Request):  # type: ignore[no-untyped-def]
    """读 app.state.skill_registry（用户领地挂载）；未装配返回 None。"""
    return getattr(request.app.state, "skill_registry", None)


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(request: Request) -> list[SkillOut]:
    registry = _registry(request)
    if registry is None:
        return []
    return [_to_out(p) for p in registry.list_packages()]


@router.post("/skills", response_model=SkillOut, status_code=201)
async def upload_skill(request: Request, file: SkillZip) -> SkillOut:
    registry = _registry(request)
    if registry is None:
        raise LubanError(ErrorCode.CONFIG_ERROR, "SkillRegistry 未装配")
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > _MAX_SKILL_ZIP_BYTES:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"Skill 包超过上限 {_MAX_SKILL_ZIP_BYTES} 字节",
                details={"size": size},
            )
        chunks.append(chunk)
    package = await registry.install(b"".join(chunks))
    return _to_out(package)


@router.delete("/skills/{key}", status_code=204)
async def delete_skill(key: str, request: Request) -> None:
    registry = _registry(request)
    if registry is None:
        raise LubanError(ErrorCode.CONFIG_ERROR, "SkillRegistry 未装配")
    if registry.get(key) is None:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"Skill 不存在: {key}",
            details={"skill": key},
        )
    registry.delete(key)
    await registry.scan()  # 刷新内存视图
