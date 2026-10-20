"""Prompt 仓储：库内定义落库、版本快照不可变；resolve 把 Agent 引用解析成快照落地。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    AgentDefinition,
    ErrorCode,
    LubanError,
    PromptDefinition,
    PromptVersion,
)
from lubanagent.persistence.models import PromptRow, PromptVersionRow


class PromptRepository:
    """与 AgentRepository 同型：指针 + 不可变快照 + UniqueConstraint。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_prompt(self, definition: PromptDefinition) -> PromptDefinition:
        """落 prompts 行 + version=definition.version 的 prompt_versions 行。"""
        row = PromptRow(
            key=definition.key,
            name=definition.name,
            description=definition.description,
            current_version=definition.version,
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add(
            PromptVersionRow(
                prompt_id=row.id,
                version=definition.version,
                content=definition.content,
                variables=definition.variables,
            )
        )
        await self._session.flush()
        return definition

    async def get_prompt(self, key: str) -> PromptDefinition | None:
        """取 prompts 行 + 当前版本合成领域对象。"""
        row = await self._session.scalar(select(PromptRow).where(PromptRow.key == key))
        if row is None:
            return None
        version_row = await self._get_version_row(row.id, row.current_version)
        if version_row is None:
            return None
        return PromptDefinition(
            key=row.key,
            name=row.name,
            description=row.description,
            content=version_row.content,
            variables=version_row.variables or {},
            version=row.current_version,
        )

    async def create_version(self, prompt_key: str, definition: PromptDefinition) -> None:
        """追加不可变版本快照并推进 current_version；重复 (key, version) 抛 VALIDATION_ERROR。"""
        row = await self._session.scalar(select(PromptRow).where(PromptRow.key == prompt_key))
        if row is None:
            raise LubanError(ErrorCode.PROMPT_NOT_FOUND, f"Prompt 不存在: {prompt_key}")
        self._session.add(
            PromptVersionRow(
                prompt_id=row.id,
                version=definition.version,
                content=definition.content,
                variables=definition.variables,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"版本已存在且不可覆盖: {prompt_key} v{definition.version}",
                details={"prompt_key": prompt_key, "version": definition.version},
            ) from e
        row.current_version = definition.version
        await self._session.flush()

    async def get_version(self, prompt_key: str, version: int) -> PromptVersion | None:
        row = await self._session.scalar(select(PromptRow).where(PromptRow.key == prompt_key))
        if row is None:
            return None
        version_row = await self._get_version_row(row.id, version)
        if version_row is None:
            return None
        return PromptVersion(
            prompt_key=prompt_key,
            version=version_row.version,
            content=version_row.content,
            variables=version_row.variables or {},
            created_at=version_row.created_at,
        )

    async def list_versions(self, prompt_key: str) -> list[PromptVersion]:
        row = await self._session.scalar(select(PromptRow).where(PromptRow.key == prompt_key))
        if row is None:
            return []
        version_rows = (
            await self._session.scalars(
                select(PromptVersionRow)
                .where(PromptVersionRow.prompt_id == row.id)
                .order_by(PromptVersionRow.version.desc())
            )
        ).all()
        return [
            PromptVersion(
                prompt_key=prompt_key,
                version=version_row.version,
                content=version_row.content,
                variables=version_row.variables or {},
                created_at=version_row.created_at,
            )
            for version_row in version_rows
        ]

    async def list_prompts(self) -> list[PromptDefinition]:
        rows = (await self._session.scalars(select(PromptRow).order_by(PromptRow.id))).all()
        result: list[PromptDefinition] = []
        for row in rows:
            definition = await self.get_prompt(row.key)
            if definition is not None:
                result.append(definition)
        return result

    async def _get_version_row(self, prompt_id: int, version: int) -> PromptVersionRow | None:
        return await self._session.scalar(
            select(PromptVersionRow).where(
                PromptVersionRow.prompt_id == prompt_id,
                PromptVersionRow.version == version,
            )
        )


async def resolve_prompt_reference(
    session: AsyncSession, definition: AgentDefinition
) -> AgentDefinition:
    """Agent 的 prompt 引用解析（落库前，快照落地）：

    - prompt_key 为 None → 原样返回（存量内嵌路径，零行为变化）
    - 非空 → 取 prompt 当前版（或 definition.prompt_version 钉住版），
      content 写入 instructions、变量合并（Agent 自带覆盖同名）后返回新定义。
      运行期只读 agent_versions 快照，不回查 prompts 表（回放确定性）。
    """
    if definition.prompt_key is None:
        return definition
    repo = PromptRepository(session)
    prompt = await repo.get_prompt(definition.prompt_key)
    if prompt is None:
        raise LubanError(
            ErrorCode.PROMPT_NOT_FOUND,
            f"Prompt 不存在: {definition.prompt_key}",
            details={"prompt_key": definition.prompt_key},
        )
    if definition.prompt_version is not None:
        if definition.prompt_version != prompt.version:
            pinned = await repo.get_version(definition.prompt_key, definition.prompt_version)
            if pinned is None:
                raise LubanError(
                    ErrorCode.PROMPT_VERSION_NOT_FOUND,
                    f"Prompt 版本不存在: {definition.prompt_key} v{definition.prompt_version}",
                    details={
                        "prompt_key": definition.prompt_key,
                        "version": definition.prompt_version,
                    },
                )
            content, variables, resolved_version = (
                pinned.content,
                pinned.variables,
                pinned.version,
            )
        else:
            content, variables, resolved_version = (
                prompt.content,
                prompt.variables,
                prompt.version,
            )
    else:
        content, variables, resolved_version = prompt.content, prompt.variables, prompt.version
    return definition.model_copy(
        update={
            "instructions": content,
            "prompt_variables": {**variables, **definition.prompt_variables},
            "prompt_version": resolved_version,
        }
    )
