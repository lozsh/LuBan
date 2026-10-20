"""模型中心仓储：模型注册 upsert 与大脑槽位映射，返回 core 领域对象。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import BrainProfile, ModelCapability, ModelDefinition
from lubanagent.core.model import validate_model_connection
from lubanagent.persistence.models import BrainMappingRow, ModelRow


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_model(self, definition: ModelDefinition) -> None:
        validate_model_connection(
            factory=definition.factory,
            params=definition.params,
            base_url=definition.base_url,
            api_key_env=definition.api_key_env,
        )
        row = await self._session.scalar(select(ModelRow).where(ModelRow.key == definition.key))
        if row is None:
            self._session.add(
                ModelRow(
                    key=definition.key,
                    provider=definition.provider,
                    model_name=definition.model_name,
                    factory=definition.factory,
                    capability=definition.capability.model_dump(mode="json"),
                    params=definition.params,
                    base_url=definition.base_url,
                    api_key_env=definition.api_key_env,
                    enabled=definition.enabled,
                )
            )
        else:
            # 列表不返回旧连接地址；编辑请求的空值表示沿用旧值，非清空。
            base_url = definition.base_url if definition.base_url is not None else row.base_url
            validate_model_connection(
                factory=definition.factory,
                params=definition.params,
                base_url=base_url,
                api_key_env=definition.api_key_env,
            )
            row.provider = definition.provider
            row.model_name = definition.model_name
            row.factory = definition.factory
            row.capability = definition.capability.model_dump(mode="json")
            row.params = definition.params
            row.base_url = base_url
            row.api_key_env = definition.api_key_env
            row.enabled = definition.enabled
        await self._session.flush()

    async def get_model(self, key: str) -> ModelDefinition | None:
        row = await self._session.scalar(select(ModelRow).where(ModelRow.key == key))
        return self._to_domain(row) if row is not None else None

    async def list_models(self, *, enabled_only: bool = True) -> list[ModelDefinition]:
        stmt = select(ModelRow).order_by(ModelRow.id)
        if enabled_only:
            stmt = stmt.where(ModelRow.enabled.is_(True))
        rows = (await self._session.scalars(stmt)).all()
        return [self._to_domain(row) for row in rows]

    async def set_brain_mapping(self, env: str, profile: BrainProfile, model_key: str) -> None:
        """不存在则插、存在则改。"""
        row = await self._session.scalar(
            select(BrainMappingRow).where(
                BrainMappingRow.env == env, BrainMappingRow.profile == profile.value
            )
        )
        if row is None:
            self._session.add(
                BrainMappingRow(env=env, profile=profile.value, model_key=model_key)
            )
        else:
            row.model_key = model_key
        await self._session.flush()

    async def has_brain_mapping(self, env: str, profile: BrainProfile) -> bool:
        """该 (env, profile) 槽位是否已有映射行（不校验指向的模型是否存在）。"""
        row_id = await self._session.scalar(
            select(BrainMappingRow.id).where(
                BrainMappingRow.env == env, BrainMappingRow.profile == profile.value
            )
        )
        return row_id is not None

    async def get_brain_mapping(self, env: str, profile: BrainProfile) -> ModelDefinition | None:
        """join models 取完整定义；映射指向的 model 不存在则 None。"""
        mapping = await self._session.scalar(
            select(BrainMappingRow).where(
                BrainMappingRow.env == env, BrainMappingRow.profile == profile.value
            )
        )
        if mapping is None:
            return None
        return await self.get_model(mapping.model_key)

    def _to_domain(self, row: ModelRow) -> ModelDefinition:
        # 历史脏记录仍可供管理界面安全展示；不能绕过构造入口执行。
        return ModelDefinition.model_construct(
            key=row.key,
            provider=row.provider,
            model_name=row.model_name,
            factory=row.factory,
            capability=ModelCapability.model_validate(row.capability),
            params=row.params,
            base_url=row.base_url,
            api_key_env=row.api_key_env,
            enabled=row.enabled,
        )
