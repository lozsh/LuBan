"""H08 部署契约：镜像秘密递归排除、演示端口 loopback、种子"只填不存在"、--fill-missing。

配置类断言按计划允许以确定性配置断言替代运行行为测试；Docker 实际构建上下文
canary 验证属 H14 的隔离 Docker 纵切，不在本文件承诺。
"""

import asyncio
from pathlib import Path

import pytest
import yaml

from lubanagent.app.lifespan import seed_defaults
from lubanagent.cli.main import _init_async
from lubanagent.config import defaults, get_settings
from lubanagent.core import AgentDefinition, BrainProfile, ModelDefinition
from lubanagent.persistence import AgentRepository, Database, ModelRepository

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- .dockerignore


def _dockerignore_patterns() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _matches(path: str, pattern: str) -> bool:
    """dockerignore 语义的保守近似：`/` 开头锚定根、`**/` 任意层级、其余按段 fnmatch。"""
    import fnmatch

    pat = pattern.lstrip("!").rstrip("/")
    if pat.startswith("/"):
        return fnmatch.fnmatch(path, pat.lstrip("/")) or any(
            fnmatch.fnmatch(path, f"{pat.lstrip('/')}/{suffix}")
            for suffix in ("*", "**")
        )
    parts = path.split("/")
    if pat.startswith("**/"):
        suffix = pat[3:]
        return any(
            fnmatch.fnmatch("/".join(parts[i:]), suffix)
            or fnmatch.fnmatch("/".join(parts[i:]), f"{suffix}/**")
            for i in range(len(parts))
        )
    return any(
        fnmatch.fnmatch(part, pat)
        or fnmatch.fnmatch("/".join(parts[: i + 1]), pat)  # 目录前缀排除语义（dir/ 拦子树）
        for i, part in enumerate(parts)
    )


def test_dockerignore_excludes_secrets_and_runtime_files_recursively() -> None:
    patterns = _dockerignore_patterns()
    excludes = [p for p in patterns if not p.startswith("!")]
    canaries = [
        ".env",
        "app/config/.env.local",  # 嵌套 env 变体
        "scripts/private.pem",
        "deploy/tls.key",
        "certs/bundle.p12",
        "keys/legacy.pfx",
        ".ssh/id_rsa",
        ".ssh/id_ed25519",
        "data/avatars/customer-service-bot/avatar.png",  # 运行数据
        "demos/customer_service/data/refunds.json",
    ]
    for canary in canaries:
        assert any(_matches(canary, p) for p in excludes), f"{canary} 未被 .dockerignore 排除"


def test_dockerignore_keeps_sample_env_after_recursive_exclusion() -> None:
    """反排除出现在递归排除之后，且样本路径命中反排除。"""
    patterns = _dockerignore_patterns()
    assert patterns.index("**/.env.*") < patterns.index("!app/config/.env.example")
    negations = [p for p in patterns if p.startswith("!")]
    assert "!app/config/.env.example" in negations


# ------------------------------------------------------------- docker-compose


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_demo_ports_bind_loopback_only() -> None:
    compose = _compose()
    for name, service in compose["services"].items():
        for port in service.get("ports") or []:
            host_side = str(port).split(":")[0]
            assert host_side == "127.0.0.1", f"{name} 端口 {port} 未绑 loopback"
    # mock-biz 仅容器网络互通，不映射宿主端口
    assert "ports" not in compose["services"]["mock-biz"]


def test_compose_persists_knowledge_and_avatar_files() -> None:
    compose = _compose()
    app_env = compose["services"]["app"]["environment"]
    assert app_env["KNOWLEDGE_FILES_ROOT"] == "/data/knowledge"
    assert app_env["AVATAR_FILES_ROOT"] == "/data/avatars"
    volumes = set(compose["volumes"])
    assert {"knowledge-data", "avatars-data"} <= volumes
    app_volumes = compose["services"]["app"]["volumes"]
    assert "knowledge-data:/data/knowledge" in app_volumes
    assert "avatars-data:/data/avatars" in app_volumes


def test_compose_auth_disabled_only_in_demo_app_service() -> None:
    compose = _compose()
    disabled = [
        name
        for name, service in compose["services"].items()
        if (service.get("environment") or {}).get("AUTH_ENABLED") == "false"
    ]
    assert disabled == ["app"]
    # 注释标注演示边界
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "AUTH_ENABLED" in text and "演示" in text


# ------------------------------------------------------------- scripts/dev.sh


def test_dev_sh_binds_loopback() -> None:
    text = (ROOT / "scripts/dev.sh").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in text  # uvicorn
    assert "--host 127.0.0.1 --port $PORT_UI --strictPort" in text  # vite
    assert "init --fill-missing" in text  # 自动启动不打回工作台修改


# ------------------------------------------------------------- 种子只填不存在


@pytest.fixture
def seed_db(tmp_path: Path) -> Database:
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/seed.db")

    async def _create() -> None:
        from lubanagent.persistence import Base

        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return db


async def test_seed_only_fills_missing_model_definitions(seed_db: Database) -> None:
    """空库 → 种子补齐；已存在同 key 定义（用户改过）→ 不被打回。"""
    user_definition = ModelDefinition.model_validate(
        {**defaults.SEED_MODEL_DEFINITIONS[0], "params": {"script": ["用户自定义台词"]}}
    )
    async with seed_db.session() as session:
        await ModelRepository(session).upsert_model(user_definition)
    await seed_defaults(seed_db)
    async with seed_db.session() as session:
        stored = await ModelRepository(session).get_model(defaults.FAKE_MODEL_KEY)
    assert stored is not None
    assert stored.params == {"script": ["用户自定义台词"]}  # 用户修改保留


async def test_seed_fills_brain_mapping_only_when_missing(seed_db: Database) -> None:
    env = get_settings().env
    async with seed_db.session() as session:
        repo = ModelRepository(session)
        # 用户自建模型 + 指向它的 smart 映射
        await repo.upsert_model(
            ModelDefinition.model_validate(defaults.SEED_MODEL_DEFINITIONS[0])
            .model_copy(update={"key": "user-model"})
        )
        await repo.set_brain_mapping(env, BrainProfile.SMART, "user-model")
    await seed_defaults(seed_db)
    async with seed_db.session() as session:
        mapping = await ModelRepository(session).get_brain_mapping(env, BrainProfile.SMART)
    assert mapping is not None and mapping.key == "user-model"  # 用户映射不被种子打回


# ------------------------------------------------------------- init --fill-missing


def _sample_definition(instruction: str) -> AgentDefinition:
    return AgentDefinition(
        key="h08-agent",
        name="h08-agent",
        instructions=instruction,
    )


async def test_fill_missing_skips_existing_and_explicit_import_appends_version(
    tmp_path: Path,
) -> None:
    """--fill-missing：已存在跳过不追加版本；显式导入内容变化仍追加版本。"""
    url = f"sqlite+aiosqlite:///{tmp_path}/init.db"

    from lubanagent.persistence import Base

    db = Database(url)
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await db.close()

    definition = _sample_definition("v1 指令")
    created = await _init_async([definition], url)
    assert created == [("h08-agent", 1, False)]

    # 工作台修改：DB 内容演进到 v2
    async with Database(url).session() as session:
        repo = AgentRepository(session)
        existing = await repo.get_agent("h08-agent")
        assert existing is not None
        updated = _sample_definition("工作台修改后的指令")
        updated.version = existing.version + 1
        await repo.create_version("h08-agent", updated)

    # --fill-missing：磁盘旧 YAML（v1 内容）不打回
    skipped = await _init_async([definition], url, fill_missing=True)
    assert skipped == [("h08-agent", 2, True)]
    async with Database(url).session() as session:
        agent = await AgentRepository(session).get_agent("h08-agent")
    assert agent is not None and agent.version == 2

    # 显式导入（默认）：内容与当前版本不一致 → 追加版本
    imported = await _init_async([definition], url)
    assert imported == [("h08-agent", 3, False)]
