"""模型中心路由：模型注册（脱敏输出）与大脑槽位映射。"""

from typing import Annotated

from fastapi import APIRouter, Query

from lubanagent.api.deps import CtxDep
from lubanagent.api.schemas import (
    BrainMappingOut,
    BrainMappingSetRequest,
    BrainProfileCreate,
    BrainProfileOut,
    BrainProfilePatch,
    BrainProfileReferencesOut,
    BrainProfilesOut,
    ModelDefinitionOut,
)
from lubanagent.core import BrainProfile, ErrorCode, LubanError, ModelDefinition
from lubanagent.core.model import redact_registered_model_params
from lubanagent.persistence import ModelRepository

router = APIRouter(tags=["models"])


def _mask(definition: ModelDefinition) -> ModelDefinitionOut:
    """脱敏：不输出 base_url 等连接信息，api_key_env 只是变量名可输出。"""
    return ModelDefinitionOut(
        key=definition.key,
        provider=definition.provider,
        model_name=definition.model_name,
        factory=definition.factory,
        capability=definition.capability,
        params=redact_registered_model_params(definition.factory, definition.params),
        api_key_env=definition.api_key_env,
        enabled=definition.enabled,
    )


@router.get("/models", response_model=list[ModelDefinitionOut])
async def list_models(ctx: CtxDep) -> list[ModelDefinitionOut]:
    async with ctx.db.session() as session:
        definitions = await ModelRepository(session).list_models()
    return [_mask(definition) for definition in definitions]


@router.put("/models", response_model=ModelDefinitionOut)
async def upsert_model(definition: ModelDefinition, ctx: CtxDep) -> ModelDefinitionOut:
    async with ctx.db.session() as session:
        await ModelRepository(session).upsert_model(definition)
    return _mask(definition)


@router.get("/brain-mappings", response_model=list[BrainMappingOut])
async def list_brain_mappings(
    ctx: CtxDep, env: Annotated[str | None, Query(min_length=1, max_length=32)] = None
) -> list[BrainMappingOut]:
    """当前环境的真实映射；槽位来自数据库目录，不再枚举四个预设。"""
    env = env or ctx.settings.env
    async with ctx.db.session() as session:
        mappings = await ModelRepository(session).list_brain_mappings(env)
    return [BrainMappingOut(**mapping) for mapping in mappings]


@router.put("/brain-mappings", response_model=BrainMappingOut)
async def set_brain_mapping(body: BrainMappingSetRequest, ctx: CtxDep) -> BrainMappingOut:
    env = body.env or ctx.settings.env
    async with ctx.db.session() as session:
        repo = ModelRepository(session)
        if await repo.get_model(body.model_key) is None:
            raise LubanError(
                ErrorCode.MODEL_NOT_FOUND,
                f"模型不存在: {body.model_key}",
                details={"model_key": body.model_key},
            )
        await repo.set_brain_mapping(env, body.profile, body.model_key)
        slot = await repo.require_brain_profile(body.profile)
    return BrainMappingOut(env=env, profile=BrainProfile(slot.key), model_key=body.model_key)


@router.get("/brain-profiles", response_model=BrainProfilesOut)
async def list_brain_profiles(ctx: CtxDep) -> BrainProfilesOut:
    async with ctx.db.session() as session:
        slots = await ModelRepository(session).list_brain_profiles()
    return BrainProfilesOut(env=ctx.settings.env, items=slots)


@router.post("/brain-profiles", response_model=BrainProfileOut, status_code=201)
async def create_brain_profile(body: BrainProfileCreate, ctx: CtxDep) -> BrainProfileOut:
    async with ctx.db.session() as session:
        repo = ModelRepository(session)
        slot = await repo.create_brain_profile(
            body.key, name=body.name, description=body.description
        )
        if body.model_key is not None:
            if await repo.get_model(body.model_key) is None:
                raise LubanError(ErrorCode.MODEL_NOT_FOUND, f"模型不存在: {body.model_key}")
            await repo.set_brain_mapping(body.env or ctx.settings.env, body.key, body.model_key)
    return BrainProfileOut(**slot)


@router.patch("/brain-profiles/{key}", response_model=BrainProfileOut)
async def update_brain_profile(
    key: BrainProfile, body: BrainProfilePatch, ctx: CtxDep
) -> BrainProfileOut:
    async with ctx.db.session() as session:
        repo = ModelRepository(session)
        slot = await repo.update_brain_profile(
            key, key=body.key, name=body.name, description=body.description
        )
        if body.model_key is not None:
            if await repo.get_model(body.model_key) is None:
                raise LubanError(ErrorCode.MODEL_NOT_FOUND, f"模型不存在: {body.model_key}")
            await repo.set_brain_mapping(
                body.env or ctx.settings.env, BrainProfile(slot["key"]), body.model_key
            )
    return BrainProfileOut(**slot)


@router.get("/brain-profiles/{key}/references", response_model=BrainProfileReferencesOut)
async def brain_profile_references(key: BrainProfile, ctx: CtxDep) -> BrainProfileReferencesOut:
    async with ctx.db.session() as session:
        repo = ModelRepository(session)
        slot = await repo.require_brain_profile(key)
        refs = await repo.brain_profile_references(slot)
    return BrainProfileReferencesOut(
        key=slot.key, agent_count=len({r["agent_key"] for r in refs}), references=refs
    )


@router.delete("/brain-profiles/{key}")
async def delete_brain_profile(key: BrainProfile, ctx: CtxDep) -> dict[str, bool]:
    async with ctx.db.session() as session:
        await ModelRepository(session).delete_brain_profile(key)
    return {"removed": True}
