"""模型中心仓储：模型注册 upsert 与大脑槽位映射，返回 core 领域对象。"""

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    ErrorCode,
    LubanError,
    ModelCapability,
    ModelDefinition,
)
from lubanagent.core.model import validate_model_connection
from lubanagent.persistence.models import (
    AgentRow,
    AgentVersionRow,
    BrainMappingRow,
    BrainProfileAliasRow,
    BrainProfileRow,
    ModelRow,
)


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
        slot = await self.require_brain_profile(profile, lock=True, ensure_preset=True)
        row = await self._session.scalar(
            select(BrainMappingRow).where(
                BrainMappingRow.env == env, BrainMappingRow.profile == slot.key
            )
        )
        if row is None:
            self._session.add(BrainMappingRow(env=env, profile=slot.key, model_key=model_key))
        else:
            row.model_key = model_key
        await self._session.flush()

    async def has_brain_mapping(self, env: str, profile: BrainProfile) -> bool:
        """该 (env, profile) 槽位是否已有映射行（不校验指向的模型是否存在）。"""
        slot = await self._find_brain_profile(profile.value)
        if slot is None or slot.deleted:
            return False
        row_id = await self._session.scalar(
            select(BrainMappingRow.id).where(
                BrainMappingRow.env == env, BrainMappingRow.profile == slot.key
            )
        )
        return row_id is not None

    async def get_brain_mapping(self, env: str, profile: BrainProfile) -> ModelDefinition | None:
        """别名、当前标识、映射及模型一次 join，改名并发时不落在两次查询的空窗。"""
        row = await self._session.scalar(
            select(ModelRow)
            .join(BrainMappingRow, BrainMappingRow.model_key == ModelRow.key)
            .join(BrainProfileRow, BrainProfileRow.key == BrainMappingRow.profile)
            .join(BrainProfileAliasRow, BrainProfileAliasRow.profile_id == BrainProfileRow.id)
            .where(
                BrainProfileAliasRow.key == profile.value,
                BrainProfileRow.deleted.is_(False),
                BrainMappingRow.env == env,
            )
        )
        return self._to_domain(row) if row is not None else None

    async def list_brain_mappings(self, env: str) -> list[dict[str, str]]:
        rows = (
            await self._session.scalars(
                select(BrainMappingRow)
                .join(BrainProfileRow, BrainProfileRow.key == BrainMappingRow.profile)
                .where(BrainMappingRow.env == env, BrainProfileRow.deleted.is_(False))
                .order_by(BrainProfileRow.id)
            )
        ).all()
        return [
            {"env": row.env, "profile": row.profile, "model_key": row.model_key} for row in rows
        ]

    async def _find_brain_profile(
        self, key: str, *, current_read: bool = False
    ) -> BrainProfileRow | None:
        stmt = (
            select(BrainProfileRow)
            .join(BrainProfileAliasRow, BrainProfileAliasRow.profile_id == BrainProfileRow.id)
            .where(BrainProfileAliasRow.key == key)
        )
        if current_read:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt.execution_options(populate_existing=True))

    async def has_brain_profile(self, profile: BrainProfile) -> bool:
        row = await self._find_brain_profile(profile.value)
        return row is not None and not row.deleted

    async def _insert_brain_profile(
        self, key: str, name: str, description: str = ""
    ) -> BrainProfileRow:
        # 当前标识和历史别名共用一张主键表：并发新增/改名也不能抢占旧标识。
        row = BrainProfileRow(key=key, name=name, description=description)
        self._session.add(row)
        await self._session.flush()
        self._session.add(BrainProfileAliasRow(key=key, profile_id=row.id))
        await self._session.flush()
        return row

    async def _lock_brain_profile(self, row: BrainProfileRow) -> None:
        # UPDATE 是跨 SQLite/MySQL 的事务写锁；FOR UPDATE 在 SQLite 不提供保护。
        await self._session.execute(
            update(BrainProfileRow)
            .where(BrainProfileRow.id == row.id)
            .values(revision=BrainProfileRow.revision + 1)
            .execution_options(synchronize_session=False)
        )
        await self._session.refresh(row, with_for_update=True)

    async def _begin_catalog_write(self) -> None:
        if self._session.get_bind().dialect.name == "sqlite":
            # aiosqlite 的 legacy transaction 不会为 SELECT 开 BEGIN；先开启真正的
            # 写事务，避免首个 SAVEPOINT RELEASE 提前提交，后续失败无法整体回滚。
            await self._session.execute(
                update(BrainProfileRow).where(False).values(revision=BrainProfileRow.revision)
            )

    async def require_brain_profile(
        self, profile: BrainProfile, *, lock: bool = False, ensure_preset: bool = False
    ) -> BrainProfileRow:
        if lock or ensure_preset:
            await self._begin_catalog_write()
        # 别名不会转移身份，先查 id，再只锁稳定目录行，避免 alias→profile 锁序倒置。
        row = await self._find_brain_profile(profile.value)
        if row is None and ensure_preset and profile.value in {p.value for p in BrainProfile}:
            # 兼容直接仓储建库/测试；存在墓碑或别名时绝不重新播种。
            try:
                async with self._session.begin_nested():
                    row = await self._insert_brain_profile(profile.value, profile.value)
            except IntegrityError:
                row = await self._find_brain_profile(profile.value, current_read=True)
        if row is None:
            raise LubanError(
                ErrorCode.BRAIN_PROFILE_NOT_FOUND,
                f"大脑槽位不存在: {profile.value}",
                details={"profile": profile.value},
            )
        if lock:
            await self._lock_brain_profile(row)
        if row.deleted:
            raise LubanError(
                ErrorCode.BRAIN_PROFILE_NOT_FOUND,
                f"大脑槽位已删除: {profile.value}",
                details={"profile": profile.value},
            )
        return row

    async def seed_brain_profiles(self, keys: list[str]) -> None:
        await self._begin_catalog_write()
        for key in keys:
            if await self._find_brain_profile(key) is not None:
                continue
            try:
                async with self._session.begin_nested():
                    await self._insert_brain_profile(key, key)
            except IntegrityError:
                # 另一进程已经播种；不得覆盖其用户编辑或删除状态。
                pass

    async def _profile_aliases(
        self, row: BrainProfileRow, *, current_read: bool = False
    ) -> list[str]:
        stmt = (
            select(BrainProfileAliasRow.key)
            .where(BrainProfileAliasRow.profile_id == row.id, BrainProfileAliasRow.key != row.key)
            .order_by(BrainProfileAliasRow.key)
        )
        if current_read:
            stmt = stmt.with_for_update()
        return list((await self._session.scalars(stmt)).all())

    async def brain_profile_references(
        self, row: BrainProfileRow, *, current_read: bool = False
    ) -> list[dict[str, Any]]:
        keys = {row.key, *await self._profile_aliases(row, current_read=current_read)}
        # 版本号没有正数约束；包括 0/负数和图 stub 在内的存量快照均可加载。
        # 不能用内部 version=0 约定推断它不运行，从而漏掉实际使用依赖。
        stmt = select(AgentVersionRow)
        if current_read:
            # MySQL REPEATABLE READ 下必须当前读，不能沿用获得槽位锁之前的快照。
            # 只锁版本，不 join 锁 Agent 行，以免与其他槽位的新版本更新形成反向锁序。
            stmt = stmt.with_for_update(read=True)
        versions = (
            await self._session.scalars(stmt.execution_options(populate_existing=True))
        ).all()
        matches = []
        for version in versions:
            definition = version.definition
            brain = definition.get("brain") or {}
            fields = []
            if brain.get("profile", "smart") in keys:
                fields.append("brain.profile")
            if any(key in keys for key in brain.get("fallback_profiles", [])):
                fields.append("brain.fallback_profiles")
            if (definition.get("memory") or {}).get("summary", False) and "cheap" in keys:
                fields.append("memory.summary")
            if fields:
                matches.append((version, fields))
        if not matches:
            return []
        agent_stmt = select(AgentRow).where(
            AgentRow.id.in_({version.agent_id for version, _ in matches})
        )
        if current_read:
            agent_stmt = agent_stmt.with_for_update(read=True)
        agents = {
            row.id: row
            for row in (
                await self._session.scalars(agent_stmt.execution_options(populate_existing=True))
            ).all()
        }
        refs = [
            {
                "agent_key": agents[version.agent_id].key,
                "agent_name": version.definition.get("name", agents[version.agent_id].name),
                "version": version.version,
                "current": agents[version.agent_id].current_version == version.version,
                "field": field,
            }
            for version, fields in matches
            if version.agent_id in agents
            for field in fields
        ]
        return sorted(refs, key=lambda ref: (ref["agent_key"], ref["version"], ref["field"]))

    async def _profile_out(self, row: BrainProfileRow) -> dict[str, Any]:
        refs = await self.brain_profile_references(row)
        return {
            "key": row.key,
            "name": row.name,
            "description": row.description,
            "aliases": await self._profile_aliases(row),
            "reference_count": len(refs),
            "agent_count": len({ref["agent_key"] for ref in refs}),
        }

    async def list_brain_profiles(self) -> list[dict[str, Any]]:
        rows = (
            await self._session.scalars(
                select(BrainProfileRow)
                .where(BrainProfileRow.deleted.is_(False))
                .order_by(BrainProfileRow.id)
            )
        ).all()
        return [await self._profile_out(row) for row in rows]

    async def create_brain_profile(
        self, key: BrainProfile, *, name: str, description: str = ""
    ) -> dict[str, Any]:
        await self._begin_catalog_write()
        row = await self._find_brain_profile(key.value)
        if row is not None:
            await self._lock_brain_profile(row)
            if not row.deleted or row.key != key.value:
                raise LubanError(
                    ErrorCode.BRAIN_PROFILE_CONFLICT, f"槽位标识或旧别名已存在: {key.value}"
                )
            row.deleted = False
            row.name = name
            row.description = description
        else:
            try:
                async with self._session.begin_nested():
                    row = await self._insert_brain_profile(key.value, name, description)
            except IntegrityError as exc:
                raise LubanError(
                    ErrorCode.BRAIN_PROFILE_CONFLICT, f"槽位标识已存在: {key.value}"
                ) from exc
        await self._session.flush()
        return await self._profile_out(row)

    async def update_brain_profile(
        self,
        profile: BrainProfile,
        *,
        key: BrainProfile | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        row = await self.require_brain_profile(profile, lock=True)
        if key is not None and key.value != row.key:
            target = await self._find_brain_profile(key.value)
            if target is not None and target.id != row.id:
                raise LubanError(
                    ErrorCode.BRAIN_PROFILE_CONFLICT, f"槽位标识或旧别名已存在: {key.value}"
                )
            old_key = row.key
            try:
                async with self._session.begin_nested():
                    row.key = key.value
                    # 与新增保持同样的唯一索引锁序：先当前 key，再统一标识表。
                    await self._session.flush()
                    if target is None:
                        self._session.add(BrainProfileAliasRow(key=key.value, profile_id=row.id))
                        await self._session.flush()
                    await self._session.execute(
                        update(BrainMappingRow)
                        .where(BrainMappingRow.profile == old_key)
                        .values(profile=key.value)
                    )
                    await self._session.flush()
            except IntegrityError as exc:
                raise LubanError(
                    ErrorCode.BRAIN_PROFILE_CONFLICT, f"槽位标识或旧别名已存在: {key.value}"
                ) from exc
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        await self._session.flush()
        return await self._profile_out(row)

    async def delete_brain_profile(self, profile: BrainProfile) -> None:
        row = await self.require_brain_profile(profile, lock=True)
        refs = await self.brain_profile_references(row, current_read=True)
        if refs:
            raise LubanError(
                ErrorCode.BRAIN_PROFILE_IN_USE,
                f"槽位 {row.key} 正被 Agent 使用，不能删除",
                details={
                    "profile": row.key,
                    "references": refs,
                    "agent_count": len({r["agent_key"] for r in refs}),
                },
            )
        await self._session.execute(
            delete(BrainMappingRow).where(BrainMappingRow.profile == row.key)
        )
        row.deleted = True
        await self._session.flush()

    async def normalize_agent_brain(self, definition: AgentDefinition) -> AgentDefinition:
        """持锁校验主/降级/摘要槽位，与删除事务共用同一把行锁。"""
        profiles = {definition.brain.profile, *definition.brain.fallback_profiles}
        if definition.memory.summary:
            profiles.add(BrainProfile.CHEAP)
        rows = {}
        for profile in sorted(profiles, key=lambda item: item.value):
            rows[profile] = await self.require_brain_profile(profile, ensure_preset=True)
        # 按稳定 id 加锁且别名去重，避免两个 Agent 用不同旧名时锁序倒置。
        for row in sorted({row.id: row for row in rows.values()}.values(), key=lambda row: row.id):
            await self._lock_brain_profile(row)
            if row.deleted:
                raise LubanError(ErrorCode.BRAIN_PROFILE_NOT_FOUND, f"大脑槽位已删除: {row.key}")
        resolved = {profile: BrainProfile(row.key) for profile, row in rows.items()}
        brain = definition.brain.model_copy(
            update={
                "profile": resolved[definition.brain.profile],
                "fallback_profiles": [
                    resolved[profile] for profile in definition.brain.fallback_profiles
                ],
            }
        )
        return definition.model_copy(update={"brain": brain})

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
