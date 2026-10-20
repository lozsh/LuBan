"""skill 测试夹具：tmp_path 造标准 Skill 包 + SQLite 会话。"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.persistence import Base, Database


def write_skill(
    root: Path,
    name: str,
    *,
    description: str = "演示技能",
    version: str = "1.0.0",
    body: str = "这是正文。",
    scripts: dict[str, str] | None = None,
    references: dict[str, str] | None = None,
    assets: dict[str, str] | None = None,
) -> Path:
    """在 root 下写一个符合 Agent Skills 开放标准的包目录，返回包目录。"""
    package_dir = root / name
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: {version}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    for sub, files in (("scripts", scripts), ("references", references), ("assets", assets)):
        for rel, content in (files or {}).items():
            target = package_dir / sub / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return package_dir


@pytest.fixture
async def session(tmp_path) -> AsyncSession:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        yield session
    await database.close()
