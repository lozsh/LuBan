"""Settings 契约测试：零配置、env 覆盖、分层加载、驱动映射。"""

from pathlib import Path

import pytest

from lubanagent.config import Settings, get_settings
from lubanagent.config.defaults import DEFAULT_SQLITE_URL, USER_EXTENSION_PACKAGES

ENV_KEYS = [
    "LUBAN_ENV",
    "DEBUG",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "DATABASE_URL",
    "OBSERVABILITY_BACKEND",
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGSMITH_API_KEY",
    "AUTH_ENABLED",
    "API_KEY",
    "RATE_LIMIT_PER_MINUTE",
    "BIZ_BASE_URL",
    "EXTENSION_PACKAGES",
]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """清空相关环境变量并切到空目录，隔离真实环境与仓库根 .env。"""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_zero_config_defaults() -> None:
    s = Settings()
    assert s.env == "dev"
    assert s.database_url == DEFAULT_SQLITE_URL
    assert s.observability_backend == "langfuse"
    assert s.auth_enabled is True
    assert s.rate_limit_per_minute == 600
    assert s.extension_packages == list(USER_EXTENSION_PACKAGES)


def test_env_vars_take_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@db:3306/luban")
    monkeypatch.setenv("OBSERVABILITY_BACKEND", "none")
    monkeypatch.setenv("LUBAN_ENV", "prod")
    s = Settings()
    assert s.database_url == "mysql+asyncmy://u:p@db:3306/luban"
    assert s.observability_backend == "none"
    assert s.env == "prod"


def test_load_layered_override(monkeypatch: pytest.MonkeyPatch, isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///./base.db\nLOG_LEVEL=DEBUG\n", encoding="utf-8"
    )
    (isolated_env / ".env.staging").write_text(
        "DATABASE_URL=mysql+asyncmy://u:p@staging:3306/luban\n", encoding="utf-8"
    )
    monkeypatch.setenv("LUBAN_ENV", "staging")

    s = Settings.load()
    # .env.staging 覆盖 .env
    assert s.database_url == "mysql+asyncmy://u:p@staging:3306/luban"
    # 仅 .env 有的键仍然生效
    assert s.log_level == "DEBUG"

    # 直接构造只读 .env，不做分层
    s_plain = Settings()
    assert s_plain.database_url == "sqlite+aiosqlite:///./base.db"


def test_real_env_beats_files(monkeypatch: pytest.MonkeyPatch, isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///./base.db\n", encoding="utf-8"
    )
    (isolated_env / ".env.dev").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///./dev.db\n", encoding="utf-8"
    )
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@real:3306/luban")
    s = Settings.load()
    assert s.database_url == "mysql+asyncmy://u:p@real:3306/luban"


def test_extension_packages_csv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTENSION_PACKAGES", "myapp.tools, myapp.graphs")
    s = Settings()
    assert s.extension_packages == ["myapp.tools", "myapp.graphs"]


def test_sync_database_url_driver_mapping() -> None:
    assert (
        Settings(database_url="sqlite+aiosqlite:///./x.db").sync_database_url
        == "sqlite+pysqlite:///./x.db"
    )
    assert (
        Settings(database_url="mysql+asyncmy://u:p@h/db").sync_database_url
        == "mysql+pymysql://u:p@h/db"
    )
    # 未知方言原样返回
    plain = "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url=plain).sync_database_url == plain


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_env_example_covers_all_keys() -> None:
    example = (
        Path(__file__).parents[3] / "app" / "config" / ".env.example"
    ).read_text(encoding="utf-8")
    example_keys = set()
    for line in example.splitlines():
        line = line.strip().lstrip("#").strip()  # 注释掉的占位键也算覆盖
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key.isupper():
                example_keys.add(key)
    assert set(ENV_KEYS) <= example_keys
