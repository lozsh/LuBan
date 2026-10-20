"""客服示例测试基建：Mock biz 服务 + tmp SQLite + fake brain seed。

零网络零密钥：Mock biz 以线程起子进程（stdlib http.server），Agent 用 fake 模型。
"""

import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import Field

from lubanagent.config import Settings, get_settings
from lubanagent.core import AgentDefinition, BoundaryConfig, BrainConfig, BrainProfile
from lubanagent.extension import Registry
from lubanagent.model import ScriptedFakeChatModel, fake_definition
from lubanagent.persistence import AgentRepository, Base, Database, ModelRepository

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_DATA = _REPO_ROOT / "demos" / "customer_service" / "data"


@pytest.fixture
async def db(tmp_path) -> AsyncIterator[Database]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        observability_backend="none",
        auth_enabled=False,
        env="dev",
    )


@pytest.fixture
def registry() -> Registry:
    """独立 Registry（不污染 default_registry，测后清状态）。"""
    return Registry()


@pytest.fixture
def mock_biz(tmp_path, monkeypatch, isolated_test_process):
    """启动 Mock biz 服务（线程，系统分配测试专用端口），测后关闭。

    数据用 tmp 副本：退款回写写进 tmp_path，不污染仓库 demos/customer_service/data/。
    """
    import shutil

    from demos.customer_service.mock_services import server as mock_server

    data_copy = tmp_path / "data"
    shutil.copytree(_DEMO_DATA, data_copy)
    monkeypatch.setattr(mock_server, "_DATA_DIR", data_copy)

    server = mock_server.serve(port=0)
    isolated_test_process[0].add(server.server_port)
    monkeypatch.setenv("BIZ_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    get_settings.cache_clear()
    from app.tools.customer_service.order import _business_transport

    allowed_host = "127.0.0.1"
    allowed_port = server.server_port
    requests: list[httpx.Request] = []

    class AuditedTransport(httpx.BaseTransport):
        def __init__(self):
            self._transport = httpx.HTTPTransport(trust_env=False)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.host != allowed_host or request.url.port != allowed_port:
                raise AssertionError(f"业务请求目标未授权: {request.url}")
            requests.append(request)
            return self._transport.handle_request(request)

        def close(self) -> None:
            self._transport.close()

    transport = AuditedTransport()
    token = _business_transport.set(transport)
    server.requests = requests
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        _business_transport.reset(token)
        transport.close()
        get_settings.cache_clear()


async def seed_fake_brain(
    db: Database,
    *,
    script: list[Any],
    profile: BrainProfile = BrainProfile.SMART,
    key: str = "fake-chat",
) -> None:
    """注册 fake 模型并映射到指定槽位（env=dev）。"""
    definition = fake_definition(key=key, script=script)
    async with db.session() as session:
        repo = ModelRepository(session)
        await repo.upsert_model(definition)
        await repo.set_brain_mapping("dev", profile, key)


async def create_agent(
    db: Database,
    *,
    key: str,
    instructions: str = "你是客服。",
    tools: list[str] | None = None,
    brain: BrainProfile = BrainProfile.SMART,
) -> AgentDefinition:
    definition = AgentDefinition(
        key=key,
        name=key,
        instructions=instructions,
        tools=tools or [],
        tool_signatures={},
        boundary=BoundaryConfig(max_steps=12, timeout_seconds=60),
        brain=BrainConfig(profile=brain),
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    return definition


class SequentialFakeModel(ScriptedFakeChatModel):
    """跨子 Run 顺序消费 script 的 fake（bind_tools 不拷贝 cursor）。

    received 记录每次调用收到的消息列表——用于断言 system prompt 内容变化
    （如改 instructions 后下一次 Run 行为变化）。
    """

    usage: tuple[int, int] | None = None
    received: list[Any] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "SequentialFakeModel":
        self.bound_tools = list(tools)
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        self.received.append(list(messages))
        for chunk in self._chunks_for(self._to_message(self._next())):
            yield chunk
        if self.usage is not None:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    usage_metadata={
                        "input_tokens": self.usage[0],
                        "output_tokens": self.usage[1],
                        "total_tokens": self.usage[0] + self.usage[1],
                    },
                )
            )


@pytest.fixture(autouse=True)
def sequential_models(monkeypatch):
    """create_chat_model 替换为单例 SequentialFakeModel（跨子 Run 推进同一 cursor）。

    测试可经 sequential_models[key].usage = (in, out) 注入用量触发预算熔断。
    """

    instances: dict[str, SequentialFakeModel] = {}

    def factory(definition):
        instance = instances.get(definition.key)
        if instance is None:
            instance = SequentialFakeModel(
                script=list(definition.params.get("script", [])),
            )
            instances[definition.key] = instance
        return instance

    monkeypatch.setattr("lubanagent.runtime.agent_loop.create_chat_model", factory)
    return instances
