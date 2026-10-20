"""api 测试基建：tmp SQLite + seed_defaults + httpx ASGI 客户端（不起真实端口）。"""

import httpx
import pytest

from lubanagent.app import create_app, seed_defaults
from lubanagent.config import Settings
from lubanagent.extension import Registry
from lubanagent.persistence import Base, Database


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def settings() -> Settings:
    return Settings(auth_enabled=False, observability_backend="none", env="dev")


@pytest.fixture
async def app(db: Database, settings: Settings, registry: Registry):
    application = create_app(settings=settings, registry=registry, db=db)
    await seed_defaults(db)
    return application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client


def make_settings(**overrides) -> Settings:
    base = {"auth_enabled": False, "observability_backend": "none", "env": "dev"}
    return Settings(**{**base, **overrides})


async def make_client(db: Database, registry: Registry, **overrides) -> httpx.AsyncClient:
    """按 settings 覆盖项现场装配客户端（鉴权/限流等场景的替身注入点）。"""
    application = create_app(settings=make_settings(**overrides), registry=registry, db=db)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    )


async def create_demo_agent(client: httpx.AsyncClient, key: str = "demo-agent") -> dict:
    response = await client.post(
        "/v1/agents",
        json={"key": key, "name": key, "instructions": "你是测试助手。", "tools": []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def sse_run_id(text: str) -> str:
    """从 SSE 文本取首个事件帧的 run_id。"""
    import json

    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])["run_id"]
    raise AssertionError(f"SSE 文本无 data 帧: {text!r}")
