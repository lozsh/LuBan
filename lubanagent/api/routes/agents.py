"""Agent 定义路由：CRUD 与不可变版本快照。"""

from fastapi import APIRouter

from lubanagent.api.deps import CtxDep
from lubanagent.api.errors import conflict_response
from lubanagent.core import AgentDefinition, AgentVersion, ErrorCode, LubanError
from lubanagent.persistence import AgentRepository, resolve_prompt_reference

router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=list[AgentDefinition])
async def list_agents(ctx: CtxDep) -> list[AgentDefinition]:
    async with ctx.db.session() as session:
        return await AgentRepository(session).list_agents()


@router.post("/agents", response_model=AgentDefinition, status_code=201)
async def create_agent(definition: AgentDefinition, ctx: CtxDep):
    async with ctx.db.session() as session:
        repo = AgentRepository(session)
        if await repo.get_agent(definition.key) is not None:
            return conflict_response(
                f"Agent key 重复: {definition.key}", {"agent_key": definition.key}
            )
        # Prompt 库引用（双轨）：非空时落库前解析 prompt 内容写入快照
        definition = await resolve_prompt_reference(session, definition)
        return await repo.create_agent(definition)


@router.get("/agents/{key}", response_model=AgentDefinition)
async def get_agent(key: str, ctx: CtxDep) -> AgentDefinition:
    async with ctx.db.session() as session:
        definition = await AgentRepository(session).get_agent(key)
    if definition is None:
        raise LubanError(
            ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {key}", details={"agent_key": key}
        )
    return definition


@router.post("/agents/{key}/versions", response_model=AgentVersion, status_code=201)
async def create_version(
    key: str, definition: AgentDefinition, ctx: CtxDep
):
    async with ctx.db.session() as session:
        repo = AgentRepository(session)
        if await repo.get_agent(key) is None:
            raise LubanError(
                ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {key}", details={"agent_key": key}
            )
        if await repo.get_version(key, definition.version) is not None:
            return conflict_response(
                f"版本已存在且不可覆盖: {key} v{definition.version}",
                {"agent_key": key, "version": definition.version},
            )
        # Prompt 库引用（双轨）：非空时落库前解析 prompt 内容写入快照
        definition = await resolve_prompt_reference(session, definition)
        await repo.create_version(key, definition)
        version = await repo.get_version(key, definition.version)
    return version


@router.get("/agents/{key}/versions/{version}", response_model=AgentVersion)
async def get_version(key: str, version: int, ctx: CtxDep) -> AgentVersion:
    async with ctx.db.session() as session:
        snapshot = await AgentRepository(session).get_version(key, version)
    if snapshot is None:
        async with ctx.db.session() as session:
            current = await AgentRepository(session).get_agent(key)
        code = (
            ErrorCode.AGENT_NOT_FOUND if current is None else ErrorCode.AGENT_VERSION_NOT_FOUND
        )
        raise LubanError(
            code,
            f"Agent 版本不存在: {key} v{version}",
            details={"agent_key": key, "version": version},
        )
    return snapshot
