"""Skill 仓储：SkillPackage 领域对象承载（D3-3 兑现），内容 hash 判定版本更新。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import SkillPackage
from lubanagent.persistence.models import SkillRow


class SkillRepository:
    """skills 表 = SkillPackage 序列化：key=name、version=frontmatter version、manifest=全量。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, skill: SkillPackage, *, enabled: bool = True) -> None:
        """同 key 仅当内容 hash 变化才更新 version/manifest；enabled 始终跟随入参。"""
        key = skill.meta.name
        manifest = skill.model_dump(mode="json")
        row = await self._session.scalar(select(SkillRow).where(SkillRow.key == key))
        if row is None:
            self._session.add(
                SkillRow(
                    key=key,
                    name=skill.meta.name,
                    version=skill.meta.version,
                    manifest=manifest,
                    enabled=enabled,
                )
            )
        else:
            row.name = skill.meta.name
            row.enabled = enabled
            if (row.manifest or {}).get("content_hash", "") != skill.content_hash:
                row.version = skill.meta.version
                row.manifest = manifest
        await self._session.flush()

    async def get(self, key: str) -> SkillPackage | None:
        row = await self._session.scalar(select(SkillRow).where(SkillRow.key == key))
        return SkillPackage.model_validate(row.manifest) if row is not None else None

    async def list(self) -> list[SkillPackage]:
        rows = (await self._session.scalars(select(SkillRow).order_by(SkillRow.id))).all()
        return [SkillPackage.model_validate(row.manifest) for row in rows]
