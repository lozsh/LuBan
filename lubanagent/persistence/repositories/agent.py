"""Agent 仓储：定义落库、版本快照不可变，返回 core 领域对象。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import AgentDefinition, AgentStatus, AgentVersion, ErrorCode, LubanError
from lubanagent.persistence.models import AgentRow, AgentVersionRow


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_agent(self, definition: AgentDefinition) -> AgentDefinition:
        """落 agents 行 + version=definition.version 的 agent_versions 行。"""
        row = AgentRow(
            key=definition.key,
            name=definition.name,
            description=definition.description,
            current_version=definition.version,
            status=definition.status.value,
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add(
            AgentVersionRow(
                agent_id=row.id,
                version=definition.version,
                definition=definition.model_dump(mode="json"),
                status=definition.status.value,
            )
        )
        await self._session.flush()
        return definition

    async def get_agent(self, key: str) -> AgentDefinition | None:
        """取 agents 行 + 当前版本 definition 合成领域对象。"""
        row = await self._session.scalar(select(AgentRow).where(AgentRow.key == key))
        if row is None:
            return None
        version_row = await self._get_version_row(row.id, row.current_version)
        if version_row is None:
            return None
        definition = AgentDefinition.model_validate(version_row.definition)
        definition.status = AgentStatus(row.status)
        definition.version = row.current_version
        return definition

    async def create_version(self, agent_key: str, definition: AgentDefinition) -> None:
        """追加不可变版本快照并推进 current_version；重复 (key, version) 抛 VALIDATION_ERROR。"""
        row = await self._session.scalar(select(AgentRow).where(AgentRow.key == agent_key))
        if row is None:
            raise LubanError(ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {agent_key}")
        self._session.add(
            AgentVersionRow(
                agent_id=row.id,
                version=definition.version,
                definition=definition.model_dump(mode="json"),
                status=definition.status.value,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as e:
            # flush 失败后事务已不可用，先 rollback 复位再抛领域错误
            await self._session.rollback()
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"版本已存在且不可覆盖: {agent_key} v{definition.version}",
                details={"agent_key": agent_key, "version": definition.version},
            ) from e
        row.current_version = definition.version
        await self._session.flush()

    async def get_version(self, agent_key: str, version: int) -> AgentVersion | None:
        row = await self._session.scalar(select(AgentRow).where(AgentRow.key == agent_key))
        if row is None:
            return None
        version_row = await self._get_version_row(row.id, version)
        if version_row is None:
            return None
        return AgentVersion(
            agent_key=agent_key,
            version=version_row.version,
            definition=AgentDefinition.model_validate(version_row.definition),
            status=AgentStatus(version_row.status),
            published_at=version_row.published_at,
        )

    async def list_agents(self) -> list[AgentDefinition]:
        rows = (await self._session.scalars(select(AgentRow).order_by(AgentRow.id))).all()
        result: list[AgentDefinition] = []
        for row in rows:
            definition = await self.get_agent(row.key)
            if definition is not None:
                result.append(definition)
        return result

    async def update_status(self, agent_key: str, status: AgentStatus) -> None:
        row = await self._session.scalar(select(AgentRow).where(AgentRow.key == agent_key))
        if row is None:
            raise LubanError(ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {agent_key}")
        row.status = status.value
        await self._session.flush()

    async def _get_version_row(self, agent_id: int, version: int) -> AgentVersionRow | None:
        return await self._session.scalar(
            select(AgentVersionRow).where(
                AgentVersionRow.agent_id == agent_id, AgentVersionRow.version == version
            )
        )
