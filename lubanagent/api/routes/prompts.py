"""Prompt 库路由：CRUD 与不可变版本快照（与 agents 路由同型）。"""

from fastapi import APIRouter

from lubanagent.api.deps import CtxDep
from lubanagent.api.errors import conflict_response
from lubanagent.core import ErrorCode, LubanError, PromptDefinition, PromptVersion
from lubanagent.persistence import PromptRepository

router = APIRouter(tags=["prompts"])


@router.get("/prompts", response_model=list[PromptDefinition])
async def list_prompts(ctx: CtxDep) -> list[PromptDefinition]:
    async with ctx.db.session() as session:
        return await PromptRepository(session).list_prompts()


@router.post("/prompts", response_model=PromptDefinition, status_code=201)
async def create_prompt(definition: PromptDefinition, ctx: CtxDep):
    async with ctx.db.session() as session:
        repo = PromptRepository(session)
        if await repo.get_prompt(definition.key) is not None:
            return conflict_response(
                f"Prompt key 重复: {definition.key}", {"prompt_key": definition.key}
            )
        return await repo.create_prompt(definition)


@router.get("/prompts/{key}", response_model=PromptDefinition)
async def get_prompt(key: str, ctx: CtxDep) -> PromptDefinition:
    async with ctx.db.session() as session:
        definition = await PromptRepository(session).get_prompt(key)
    if definition is None:
        raise LubanError(
            ErrorCode.PROMPT_NOT_FOUND, f"Prompt 不存在: {key}", details={"prompt_key": key}
        )
    return definition


@router.get("/prompts/{key}/versions", response_model=list[PromptVersion])
async def list_versions(key: str, ctx: CtxDep) -> list[PromptVersion]:
    async with ctx.db.session() as session:
        repo = PromptRepository(session)
        if await repo.get_prompt(key) is None:
            raise LubanError(
                ErrorCode.PROMPT_NOT_FOUND, f"Prompt 不存在: {key}", details={"prompt_key": key}
            )
        return await repo.list_versions(key)


@router.post("/prompts/{key}/versions", response_model=PromptVersion, status_code=201)
async def create_version(key: str, definition: PromptDefinition, ctx: CtxDep):
    async with ctx.db.session() as session:
        repo = PromptRepository(session)
        if await repo.get_prompt(key) is None:
            raise LubanError(
                ErrorCode.PROMPT_NOT_FOUND, f"Prompt 不存在: {key}", details={"prompt_key": key}
            )
        if await repo.get_version(key, definition.version) is not None:
            return conflict_response(
                f"版本已存在且不可覆盖: {key} v{definition.version}",
                {"prompt_key": key, "version": definition.version},
            )
        await repo.create_version(key, definition)
        version = await repo.get_version(key, definition.version)
    return version


@router.get("/prompts/{key}/versions/{version}", response_model=PromptVersion)
async def get_version(key: str, version: int, ctx: CtxDep) -> PromptVersion:
    async with ctx.db.session() as session:
        repo = PromptRepository(session)
        snapshot = await repo.get_version(key, version)
        if snapshot is None:
            current = await repo.get_prompt(key)
    if snapshot is None:
        code = (
            ErrorCode.PROMPT_NOT_FOUND if current is None else ErrorCode.PROMPT_VERSION_NOT_FOUND
        )
        raise LubanError(
            code,
            f"Prompt 版本不存在: {key} v{version}",
            details={"prompt_key": key, "version": version},
        )
    return snapshot
