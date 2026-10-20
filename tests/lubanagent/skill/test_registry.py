"""registry: scan/get/元数据格式化、load_tool 经 ToolExecutor（allowed 拒绝）、入库更新。"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.persistence.repositories import SkillRepository
from lubanagent.skill import SkillRegistry, scan_skills_dir
from lubanagent.tool import ToolExecutor

from .conftest import write_skill


async def test_scan_get_and_list_metadata(tmp_path: Path) -> None:
    write_skill(tmp_path, "refund", description="退款技能", version="1.0.0")
    write_skill(tmp_path, "faq", description="问答技能")

    registry = SkillRegistry(skills_root=tmp_path)
    packages = await registry.scan()

    assert {p.meta.name for p in packages} == {"refund", "faq"}
    assert registry.get("refund") is not None
    assert registry.get("refund").meta.version == "1.0.0"
    assert registry.get("ghost") is None
    assert {m.name for m in registry.list_metadata()} == {"refund", "faq"}


async def test_format_metadata_for_prompt(tmp_path: Path) -> None:
    write_skill(tmp_path, "refund", description="退款技能")
    write_skill(tmp_path, "faq", description="问答技能")
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.scan()

    full = registry.format_metadata_for_prompt()
    assert "- refund: 退款技能" in full
    assert "- faq: 问答技能" in full

    filtered = registry.format_metadata_for_prompt(names=["faq"])
    assert filtered == "- faq: 问答技能"


async def test_load_tool_via_executor(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "refund",
        description="退款技能",
        body="退款正文。",
        scripts={"run.py": "print('x')"},
        references={"guide.md": "指南"},
    )
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.scan()

    executor = ToolExecutor()
    tool = registry.make_load_tool()
    assert tool.descriptor.key == "skill_load"
    assert tool.descriptor.args_schema == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    result = await executor.execute(tool, {"name": "refund"})
    assert result.success, result.error
    assert result.value == {
        "body": "退款正文。",
        "scripts": ["scripts/run.py"],
        "references": ["references/guide.md"],
    }

    # 未知名 -> VALIDATION_ERROR
    unknown = await executor.execute(tool, {"name": "ghost"})
    assert not unknown.success
    assert unknown.error["code"] == "VALIDATION_ERROR"

    # 参数缺失 -> 校验失败, 不进入执行
    invalid = await executor.execute(tool, {})
    assert not invalid.success
    assert invalid.error["code"] == "VALIDATION_ERROR"
    assert invalid.attempts == 0


async def test_load_tool_allowed_filter(tmp_path: Path) -> None:
    write_skill(tmp_path, "refund", description="退款技能")
    write_skill(tmp_path, "admin", description="管理技能")
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.scan()

    executor = ToolExecutor()
    tool = registry.make_load_tool(allowed=["refund"])

    ok = await executor.execute(tool, {"name": "refund"})
    assert ok.success

    denied = await executor.execute(tool, {"name": "admin"})
    assert not denied.success
    assert denied.error["code"] == "TOOL_PERMISSION_DENIED"


async def test_scan_persists_and_content_change_updates(
    tmp_path: Path, session: AsyncSession
) -> None:
    from lubanagent.persistence.repositories import SkillRepository

    package_dir = write_skill(tmp_path, "refund", description="退款技能", version="1.0.0")
    repo = SkillRepository(session)
    registry = SkillRegistry(skills_root=tmp_path, repository=repo)

    await registry.scan()
    stored = await repo.get("refund")
    assert stored is not None
    assert stored.meta.version == "1.0.0"
    first_hash = stored.content_hash
    assert [p.meta.name for p in await repo.list()] == ["refund"]

    # 内容不变再 scan -> hash 不变, 幂等
    await registry.scan()
    assert (await repo.get("refund")).content_hash == first_hash

    # 内容变化 -> version/manifest 更新
    (package_dir / "SKILL.md").write_text(
        "---\nname: refund\ndescription: 退款技能\nversion: 1.1.0\n---\n\n新正文。\n",
        encoding="utf-8",
    )
    await registry.scan()
    updated = await repo.get("refund")
    assert updated.meta.version == "1.1.0"
    assert updated.content_hash != first_hash

    # 磁盘扫描结果与库内一致
    on_disk = scan_skills_dir(tmp_path)[0]
    assert updated.content_hash == on_disk.content_hash


async def test_scan_repository_failure_preserves_prior_view_and_rows(
    tmp_path: Path, session: AsyncSession
) -> None:
    class FailingRepository(SkillRepository):
        async def upsert(self, skill, *, enabled=True):
            await super().upsert(skill, enabled=enabled)
            if skill.meta.name == "z":
                raise RuntimeError("repo failed")

    write_skill(tmp_path, "a", version="1")
    repo = FailingRepository(session)
    registry = SkillRegistry(skills_root=tmp_path, repository=repo)
    await registry.scan()
    old = registry.get("a")
    write_skill(tmp_path, "a", version="2")
    write_skill(tmp_path, "z", version="1")
    with pytest.raises(RuntimeError, match="repo failed"):
        await registry.scan()
    assert registry.get("a") is old
    assert registry.get("z") is None
    assert (await repo.get("a")).meta.version == "1"
    assert await repo.get("z") is None
