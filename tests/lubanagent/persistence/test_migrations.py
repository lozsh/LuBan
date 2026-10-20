"""迁移冒烟：0008 eval_results 报告明细列，upgrade→downgrade→upgrade 三向可过 + 数据保留。

做法：先升到 0007 末版种一行旧结构数据（score/details），再升 head 验证列改名后
数据仍在；降级回旧结构再升级，最终用 EvalRepository（ORM 真实读写）证明
迁移后表结构与模型一致（SQLite batch 写法兜底，同 0004/0007 惯例）。
"""

import asyncio
import os
import sqlite3
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from lubanagent.cli.main import _restore_logging, _snapshot_logging
from lubanagent.config import Settings
from lubanagent.core import EvalCase, EvalDataset, EvalReport
from lubanagent.persistence import Database, EvalRepository

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRE_0008 = "c1d2e3f4a5b6"


def _alembic_run(url: str, direction: str, revision: str) -> None:
    """跑一条 alembic 命令（同 CLI migrate 的 URL 注入与 logging 快照恢复）。"""
    config = AlembicConfig(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.set_main_option(
        "sqlalchemy.url", Settings(database_url=url).sync_database_url
    )
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    snapshot = _snapshot_logging()
    try:
        if direction == "upgrade":
            alembic_command.upgrade(config, revision)
        else:
            alembic_command.downgrade(config, revision)
    finally:
        _restore_logging(snapshot)
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _sync_path(url: str) -> str:
    return url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")


def test_migration_0008_three_way_roundtrip(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/mig.db"

    # 1) 升到 0007 末版，种一行旧结构数据（score/details）
    _alembic_run(url, "upgrade", _PRE_0008)
    with sqlite3.connect(_sync_path(url)) as conn:
        conn.execute(
            "INSERT INTO eval_datasets (key, name, version, cases) VALUES ('d', '集', '1', '[]')"
        )
        conn.execute(
            "INSERT INTO eval_results (dataset_id, agent_key, agent_version, model_key,"
            " score, details) VALUES (1, 'a', 1, 'm', 0.5, '{\"results\": []}')"
        )

    # 2) 升 head：列改名（score→pass_rate、details→report）+ 新增聚合列，数据保留
    _alembic_run(url, "upgrade", "head")
    with sqlite3.connect(_sync_path(url)) as conn:
        row = conn.execute(
            "SELECT dataset_id, agent_key, pass_rate, report, passed_cases,"
            " total_cases, wrong_answer_rate FROM eval_results WHERE id = 1"
        ).fetchone()
    assert row == (1, "a", 0.5, '{"results": []}', 0, 0, 0.0)

    # 3) 降回旧结构 → 再升 head（三向冒烟）
    _alembic_run(url, "downgrade", _PRE_0008)
    with sqlite3.connect(_sync_path(url)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(eval_results)")}
    assert {"score", "details"} <= columns
    assert "pass_rate" not in columns
    _alembic_run(url, "upgrade", "head")

    # 4) 迁移后结构与 ORM 一致：仓储领域对象读写可跑
    async def _verify() -> None:
        db = Database(url)
        try:
            async with db.session() as session:
                repo = EvalRepository(session)
                await repo.create_dataset(
                    EvalDataset(key="greet", cases=[EvalCase(name="c1", input="hi")])
                )
                result_id = await repo.save_report(
                    EvalReport(dataset_key="greet", agent_key="a", agent_version=1, results=[])
                )
                detail = await repo.get_detail(result_id)
            assert detail is not None and detail.dataset_key == "greet"
        finally:
            await db.close()

    asyncio.run(_verify())
