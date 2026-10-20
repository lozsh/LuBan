"""PromptRepository：库内定义落库、版本快照不可变、resolve 引用快照落地。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import AgentDefinition, ErrorCode, LubanError, PromptDefinition
from lubanagent.persistence.repositories import (
    AgentRepository,
    PromptRepository,
    resolve_prompt_reference,
)


def make_prompt(key: str = "faq-prompt", version: int = 1, **overrides) -> PromptDefinition:
    data = {
        "key": key,
        "name": f"Prompt {key}",
        "description": "测试",
        "content": "你是 {company} 的客服 FAQ 专家。",
        "variables": {"company": "Luban 商店"},
        "version": version,
    }
    return PromptDefinition(**(data | overrides))


def make_agent(key: str = "demo", **overrides) -> AgentDefinition:
    return AgentDefinition(key=key, name=f"Demo {key}", **overrides)


async def test_create_and_get_prompt(session: AsyncSession) -> None:
    repo = PromptRepository(session)
    await repo.create_prompt(make_prompt())

    loaded = await repo.get_prompt("faq-prompt")
    assert loaded is not None
    assert loaded.key == "faq-prompt"
    assert loaded.version == 1
    assert loaded.content == "你是 {company} 的客服 FAQ 专家。"
    assert loaded.variables == {"company": "Luban 商店"}


async def test_get_prompt_missing_returns_none(session: AsyncSession) -> None:
    assert await PromptRepository(session).get_prompt("ghost") is None


async def test_create_version_immutable_and_pointer_advance(session: AsyncSession) -> None:
    repo = PromptRepository(session)
    await repo.create_prompt(make_prompt())

    v2 = make_prompt("faq-prompt", version=2, content="v2 内容")
    await repo.create_version("faq-prompt", v2)
    assert (await repo.get_prompt("faq-prompt")).version == 2

    # v1 快照仍是 v1 内容（回放确定性）
    v1 = await repo.get_version("faq-prompt", 1)
    assert v1 is not None
    assert v1.content == "你是 {company} 的客服 FAQ 专家。"


async def test_create_version_duplicate_raises(session: AsyncSession) -> None:
    """重复版本 → VALIDATION_ERROR（不可变；rollback 同 AgentRepository 语义）。"""
    repo = PromptRepository(session)
    await repo.create_prompt(make_prompt())
    with pytest.raises(LubanError) as exc_info:
        await repo.create_version("faq-prompt", make_prompt())
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_list_versions_and_missing(session: AsyncSession) -> None:
    repo = PromptRepository(session)
    await repo.create_prompt(make_prompt())
    await repo.create_version("faq-prompt", make_prompt("faq-prompt", version=2, content="v2"))

    versions = await repo.list_versions("faq-prompt")
    assert [v.version for v in versions] == [2, 1]
    assert await repo.list_versions("ghost") == []
    assert await repo.get_version("faq-prompt", 99) is None


async def test_create_version_missing_prompt(session: AsyncSession) -> None:
    with pytest.raises(LubanError) as exc_info:
        await PromptRepository(session).create_version("ghost", make_prompt("ghost"))
    assert exc_info.value.code == ErrorCode.PROMPT_NOT_FOUND


async def test_resolve_prompt_reference_none_passthrough(session: AsyncSession) -> None:
    """prompt_key=None（存量内嵌路径）→ 原样返回，零行为变化。"""
    definition = make_agent("demo", instructions="内嵌指令")
    resolved = await resolve_prompt_reference(session, definition)
    assert resolved is definition


async def test_resolve_prompt_reference_materializes(session: AsyncSession) -> None:
    """引用态：content 写入 instructions、变量合并（Agent 覆盖同名）、prompt_version 落地。"""
    await PromptRepository(session).create_prompt(make_prompt())
    definition = make_agent(
        "demo",
        prompt_key="faq-prompt",
        prompt_variables={"company": "我的商店", "extra": "x"},
    )
    resolved = await resolve_prompt_reference(session, definition)
    assert resolved.instructions == "你是 {company} 的客服 FAQ 专家。"
    assert resolved.prompt_variables == {"company": "我的商店", "extra": "x"}
    assert resolved.prompt_key == "faq-prompt"
    assert resolved.prompt_version == 1


async def test_resolve_prompt_reference_pinned_version(session: AsyncSession) -> None:
    """钉住版：prompt 已发 v2，Agent 钉 v1 → 快照取 v1 内容。"""
    repo = PromptRepository(session)
    await repo.create_prompt(make_prompt())
    await repo.create_version(
        "faq-prompt", make_prompt("faq-prompt", version=2, content="v2 内容")
    )

    definition = make_agent("demo", prompt_key="faq-prompt", prompt_version=1)
    resolved = await resolve_prompt_reference(session, definition)
    assert resolved.instructions == "你是 {company} 的客服 FAQ 专家。"
    assert resolved.prompt_version == 1

    # 未钉住 → 取当前版 v2
    resolved_current = await resolve_prompt_reference(
        session, make_agent("demo2", prompt_key="faq-prompt")
    )
    assert resolved_current.instructions == "v2 内容"
    assert resolved_current.prompt_version == 2


async def test_resolve_prompt_reference_not_found(session: AsyncSession) -> None:
    with pytest.raises(LubanError) as exc_info:
        await resolve_prompt_reference(
            session, make_agent("demo", prompt_key="ghost")
        )
    assert exc_info.value.code == ErrorCode.PROMPT_NOT_FOUND

    await PromptRepository(session).create_prompt(make_prompt())
    with pytest.raises(LubanError) as exc_info:
        await resolve_prompt_reference(
            session, make_agent("demo", prompt_key="faq-prompt", prompt_version=99)
        )
    assert exc_info.value.code == ErrorCode.PROMPT_VERSION_NOT_FOUND


async def test_agent_repository_snapshot_with_prompt_reference(session: AsyncSession) -> None:
    """端到端：Agent 带引用落库 → agent_versions 快照内嵌解析后全文（回放不回查）。"""
    await PromptRepository(session).create_prompt(make_prompt())
    definition = await resolve_prompt_reference(
        session, make_agent("demo", prompt_key="faq-prompt")
    )
    await AgentRepository(session).create_agent(definition)

    loaded = await AgentRepository(session).get_agent("demo")
    assert loaded is not None
    assert loaded.instructions == "你是 {company} 的客服 FAQ 专家。"
    assert loaded.prompt_key == "faq-prompt"
    assert loaded.prompt_version == 1
