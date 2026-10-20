"""openapi.snapshot.json 显式导出命令（D4-1 / H14 契约）。

用法：`.venv/bin/python scripts/export_openapi.py`
真实 app 装配（内存 SQLite + 默认注册表）生成 /openapi.json，
以稳定序列化（indent=2 + sort_keys）写仓库根快照。

契约测试（tests/lubanagent/api/test_openapi_snapshot.py）只比较、不写文件——
路由字段变化但未更新快照时测试失败，需显式运行本命令更新。
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lubanagent.app import create_app  # noqa: E402
from lubanagent.config import Settings  # noqa: E402
from lubanagent.persistence import Base, Database  # noqa: E402

SNAPSHOT_PATH = REPO_ROOT / "openapi.snapshot.json"


async def build_spec() -> dict:
    """真实装配（临时内存库）→ openapi.json dict（不依赖任何环境变量/真实库）。"""
    db = Database("sqlite+aiosqlite://")
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite://",
        auth_enabled=False,
        observability_backend="none",
        env="dev",
    )
    application = create_app(settings=settings, db=db)
    return application.openapi()


def main() -> None:
    spec = asyncio.run(build_spec())
    snapshot = serialize(spec)
    SNAPSHOT_PATH.write_text(snapshot, encoding="utf-8")
    print(f"已更新快照: {SNAPSHOT_PATH}（{len(snapshot)} 字节）")


def serialize(spec: dict) -> str:
    """与快照测试同一稳定序列化：sort_keys + indent=2，重复生成字节一致。"""
    import json

    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    main()
