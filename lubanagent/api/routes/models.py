"""模型中心路由：模型注册（脱敏输出）与大脑槽位映射。"""

from fastapi import APIRouter

from lubanagent.api.deps import CtxDep
from lubanagent.api.schemas import BrainMappingOut, BrainMappingSetRequest, ModelDefinitionOut
from lubanagent.core import ErrorCode, LubanError, ModelDefinition
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
async def upsert_model(
    definition: ModelDefinition, ctx: CtxDep
) -> ModelDefinitionOut:
    async with ctx.db.session() as session:
        await ModelRepository(session).upsert_model(definition)
    return _mask(definition)


@router.get("/brain-mappings", response_model=list[BrainMappingOut])
async def list_brain_mappings(ctx: CtxDep, env: str | None = None) -> list[BrainMappingOut]:
    """四槽位当前映射（工作台模型中心回显用）；env 缺省取 settings.env。"""
    from lubanagent.core import BrainProfile

    env = env or ctx.settings.env
    out: list[BrainMappingOut] = []
    async with ctx.db.session() as session:
        repo = ModelRepository(session)
        for profile in BrainProfile:
            definition = await repo.get_brain_mapping(env, profile)
            if definition is not None:
                out.append(BrainMappingOut(env=env, profile=profile, model_key=definition.key))
    return out


@router.put("/brain-mappings", response_model=BrainMappingOut)
async def set_brain_mapping(
    body: BrainMappingSetRequest, ctx: CtxDep
) -> BrainMappingOut:
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
    return BrainMappingOut(env=env, profile=body.profile, model_key=body.model_key)
