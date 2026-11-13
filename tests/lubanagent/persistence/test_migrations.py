"""迁移冒烟：0008 eval_results 报告明细列，upgrade→downgrade→upgrade 三向可过 + 数据保留。

做法：先升到 0007 末版种一行旧结构数据（score/details），再升 head 验证列改名后
数据仍在；降级回旧结构再升级，最终用 EvalRepository（ORM 真实读写）证明
迁移后表结构与模型一致（SQLite batch 写法兜底，同 0004/0007 惯例）。
"""

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from lubanagent.cli.main import _restore_logging, _snapshot_logging
from lubanagent.config import Settings
from lubanagent.core import EvalCase, EvalDataset, EvalReport
from lubanagent.persistence import Base, Database, EvalRepository, SecurityRepository
from lubanagent.persistence.repositories.log import ApplicationLogRepository

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRE_0008 = "c1d2e3f4a5b6"
_PRE_0011 = "f5a6b7c8d9e0"
_ACCESS_TABLES = frozenset(
    {
        "application_logs",
        "permissions",
        "roles",
        "role_permissions",
        "users",
        "user_roles",
        "auth_sessions",
    }
)


def _alembic_run(url: str, direction: str, revision: str) -> None:
    """跑一条 alembic 命令（同 CLI migrate 的 URL 注入与 logging 快照恢复）。"""
    config = AlembicConfig(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", Settings(database_url=url).sync_database_url)
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


def test_migration_0011_roundtrip_schema_parity_and_orm_read_write(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/logs-security.db"
    _alembic_run(url, "upgrade", _PRE_0011)
    with sqlite3.connect(_sync_path(url)) as connection:
        connection.execute(
            "INSERT INTO eval_datasets (key, name, version, cases) "
            "VALUES ('baseline', 'existing', '1', '[]')"
        )
    _alembic_run(url, "upgrade", "head")
    engine = create_engine(url.replace("+aiosqlite", ""))
    with engine.connect() as connection:
        assert _ACCESS_TABLES <= set(inspect(connection).get_table_names())
        context = MigrationContext.configure(
            connection,
            opts={
                "include_object": lambda obj, name, kind, reflected, compared: (
                    name in _ACCESS_TABLES if kind == "table" else True
                )
            },
        )
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()

    async def verify():
        from datetime import UTC, datetime

        db = Database(url)
        try:
            async with db.session() as session:
                repo = SecurityRepository(session)
                await repo.seed()
                user = await repo.create_user(
                    username="migrated-admin",
                    display_name="admin",
                    password="migration-test-password",
                    role_keys=["admin"],
                )
                token, _, _ = await repo.login(
                    username="migrated-admin", password="migration-test-password", session_hours=8
                )
                assert (await repo.user_by_token(token))["id"] == user["id"]
                await ApplicationLogRepository(session).add_batch(
                    [
                        {
                            "id": "migration-log",
                            "created_at": datetime.now(UTC),
                            "level": "INFO",
                            "logger": "test.migration",
                            "message": "persisted",
                            "pathname": "test.py",
                            "lineno": 1,
                            "extra": {},
                            "request_id": "migration-request",
                            "session_id": "migration-session",
                        }
                    ]
                )
            async with db.session() as session:
                _, total = await ApplicationLogRepository(session).list_logs(
                    request_id="migration-request"
                )
                assert total == 1
                assert await SecurityRepository(session).revoke_token(token)
        finally:
            await db.close()

    asyncio.run(verify())
    _alembic_run(url, "downgrade", _PRE_0011)
    with sqlite3.connect(_sync_path(url)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not tables & _ACCESS_TABLES
        assert connection.execute(
            "SELECT name FROM eval_datasets WHERE key = 'baseline'"
        ).fetchone() == ("existing",)
    _alembic_run(url, "upgrade", "head")
    engine = create_engine(url.replace("+aiosqlite", ""))
    with engine.connect() as connection:
        assert _ACCESS_TABLES <= set(inspect(connection).get_table_names())
    engine.dispose()


def test_new_access_tables_compile_for_mysql_without_text_indexes():
    dialect = mysql.dialect()
    for name in _ACCESS_TABLES:
        table = Base.metadata.tables[name]
        statement = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {name}" in statement
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect))
    assert next(iter(Base.metadata.tables["users"].indexes)).unique


_PRE_0012 = "a1b2c3d4e5f6"
_BRAIN_TABLES = frozenset({"brain_profiles", "brain_profile_aliases", "brain_mappings", "runs"})


def _seed_old_brain_data(url: str, *, profile: str = "smart") -> str:
    definition = json.dumps(
        {
            "key": "migrated-agent",
            "name": "Migrated",
            "brain": {"profile": profile},
            "version": 1,
            "status": "draft",
        },
        ensure_ascii=False,
    )
    with sqlite3.connect(_sync_path(url)) as connection:
        connection.execute(
            "INSERT INTO models (`key`, provider, model_name, factory, "
            "capability, params, enabled) "
            "VALUES ('m', 'fake', 'fake', 'fake', '{}', '{}', 1)"
        )
        connection.execute(
            "INSERT INTO brain_mappings (env, profile, model_key) VALUES ('dev', ?, 'm')",
            (profile,),
        )
        connection.execute(
            "INSERT INTO agents (`key`, name, description, current_version, status) "
            "VALUES ('migrated-agent', 'Migrated', '', 1, 'draft')"
        )
        connection.execute(
            "INSERT INTO agent_versions (agent_id, version, definition, status) "
            "VALUES (1, 1, ?, 'draft')",
            (definition,),
        )
        connection.execute(
            "INSERT INTO runs (id, agent_id, agent_version_id, status, input, brain_profile, "
            "token_usage, cost, output) "
            "VALUES ('migrated-run', 1, 1, 'completed', '{}', ?, '{}', 0, 'Answer')",
            (profile,),
        )
    return definition


def test_migration_0012_roundtrip_catalog_and_schema_parity(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/brain-migration.db"
    _alembic_run(url, "upgrade", _PRE_0012)
    definition = _seed_old_brain_data(url)
    _alembic_run(url, "upgrade", "head")
    for attempt in range(2):
        engine = create_engine(url.replace("+aiosqlite", ""))
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert _BRAIN_TABLES <= set(inspector.get_table_names())
            for table, column in [("runs", "brain_profile"), ("brain_mappings", "profile")]:
                assert (
                    next(item for item in inspector.get_columns(table) if item["name"] == column)[
                        "type"
                    ].length
                    == 64
                )
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_object": lambda obj, name, kind, reflected, compared: (
                        name in _BRAIN_TABLES
                        if kind == "table"
                        # 0001 的 runs.cost 为 Float，现有 ORM 为 Double（本轮前漂移），
                        # 只排除此既有列；新表、标识列、约束及其他 Run 字段仍逐项对齐。
                        else not (kind == "column" and obj.table.name == "runs" and name == "cost")
                    ),
                },
            )
            assert compare_metadata(context, Base.metadata) == []
        engine.dispose()
        with sqlite3.connect(_sync_path(url)) as connection:
            assert (
                connection.execute("SELECT definition FROM agent_versions").fetchone()[0]
                == definition
            )
            assert connection.execute("SELECT brain_profile, output FROM runs").fetchone() == (
                "smart",
                "Answer",
            )
            assert connection.execute(
                "SELECT profile, model_key FROM brain_mappings"
            ).fetchone() == ("smart", "m")
            assert {row[0] for row in connection.execute("SELECT `key` FROM brain_profiles")} == {
                "fast",
                "smart",
                "cheap",
                "vision",
            }
            assert connection.execute("SELECT COUNT(*) FROM brain_profile_aliases").fetchone() == (
                4,
            )
        if attempt == 0:
            _alembic_run(url, "downgrade", _PRE_0012)
            with sqlite3.connect(_sync_path(url)) as connection:
                assert not connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'brain_profiles'"
                ).fetchone()
                assert connection.execute("SELECT brain_profile FROM runs").fetchone() == ("smart",)
            _alembic_run(url, "upgrade", "head")


@pytest.mark.parametrize(
    "change", ["custom", "renamed", "deleted", "edited", "definition", "run", "mapping"]
)
def test_migration_0012_rejects_destructive_downgrade(tmp_path, change):
    url = f"sqlite+aiosqlite:///{tmp_path}/brain-downgrade.db"
    _alembic_run(url, "upgrade", _PRE_0012)
    _seed_old_brain_data(url)
    _alembic_run(url, "upgrade", "head")
    with sqlite3.connect(_sync_path(url)) as connection:
        if change == "custom":
            connection.execute(
                "INSERT INTO brain_profiles (`key`, name, description, deleted, revision) "
                "VALUES ('custom', 'Custom', '', 0, 0)"
            )
        elif change == "renamed":
            connection.execute(
                "UPDATE brain_profiles SET `key` = 'new-smart' WHERE `key` = 'smart'"
            )
        elif change == "deleted":
            connection.execute("UPDATE brain_profiles SET deleted = 1 WHERE `key` = 'smart'")
        elif change == "edited":
            connection.execute("UPDATE brain_profiles SET name = '推理' WHERE `key` = 'smart'")
        elif change == "definition":
            connection.execute(
                "UPDATE agent_versions SET definition = ?",
                (json.dumps({"brain": {"profile": "smart", "fallback_profiles": ["custom"]}}),),
            )
        elif change == "run":
            connection.execute("UPDATE runs SET brain_profile = 'custom'")
        else:
            connection.execute("UPDATE brain_mappings SET profile = 'custom'")
    with pytest.raises(RuntimeError, match="Cannot downgrade editable brain profiles"):
        _alembic_run(url, "downgrade", _PRE_0012)
    with sqlite3.connect(_sync_path(url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "b2c3d4e5f6a7",
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'brain_profiles'"
        ).fetchone()


def test_migration_0012_registers_legacy_custom_keys_without_rewriting_snapshots(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/brain-custom-migration.db"
    _alembic_run(url, "upgrade", _PRE_0012)
    definition = _seed_old_brain_data(url, profile="existing_custom")
    _alembic_run(url, "upgrade", "head")
    with sqlite3.connect(_sync_path(url)) as connection:
        assert (
            connection.execute("SELECT definition FROM agent_versions").fetchone()[0] == definition
        )
        assert connection.execute("SELECT brain_profile FROM runs").fetchone() == (
            "existing_custom",
        )
        assert connection.execute(
            "SELECT name FROM brain_profiles WHERE `key` = 'existing_custom'"
        ).fetchone() == ("existing_custom",)
    with pytest.raises(RuntimeError):
        _alembic_run(url, "downgrade", _PRE_0012)


def test_brain_profile_tables_compile_for_mysql():
    dialect = mysql.dialect()
    for name in _BRAIN_TABLES:
        assert f"CREATE TABLE {name}" in str(
            CreateTable(Base.metadata.tables[name]).compile(dialect=dialect)
        )
