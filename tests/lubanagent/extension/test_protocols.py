"""Protocol 契约: runtime_checkable isinstance 断言手写实现, 渠道层三扩展点重点覆盖。"""

from datetime import UTC, datetime
from typing import Any

from lubanagent.core import ToolDescriptor
from lubanagent.extension import (
    AuthRequest,
    ChannelConnector,
    GuardVerdict,
    OutputGuard,
    Principal,
    SessionState,
    SessionStateStore,
    ToolProvider,
)


class FakeSessionStateStore:
    def __init__(self) -> None:
        self._store: dict[str, SessionState] = {}

    async def get(self, session_key: str) -> SessionState | None:
        return self._store.get(session_key)

    async def save(self, state: SessionState) -> None:
        self._store[state.session_key] = state


class FakeOutputGuard:
    async def check(self, text: str, *, context: dict[str, Any]) -> GuardVerdict:
        return GuardVerdict(allowed=True, sanitized_text=text)


class FakeChannelConnector:
    @property
    def name(self) -> str:
        return "fake-channel"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeToolProvider:
    def tools(self) -> list[ToolDescriptor]:
        return []


def test_channel_protocols_runtime_checkable() -> None:
    assert isinstance(FakeSessionStateStore(), SessionStateStore)
    assert isinstance(FakeOutputGuard(), OutputGuard)
    assert isinstance(FakeChannelConnector(), ChannelConnector)
    assert isinstance(FakeToolProvider(), ToolProvider)


def test_channel_protocols_negative() -> None:
    assert not isinstance(object(), SessionStateStore)
    assert not isinstance(object(), OutputGuard)
    assert not isinstance(object(), ChannelConnector)


async def test_channel_implementations_behavior() -> None:
    store = FakeSessionStateStore()
    state = SessionState(
        session_key="s1", state="ai_serving", updated_at=datetime.now(UTC)
    )
    await store.save(state)
    assert await store.get("s1") == state
    assert await store.get("nope") is None

    verdict = await FakeOutputGuard().check("你好", context={})
    assert verdict.allowed is True
    assert verdict.sanitized_text == "你好"


def test_contract_models() -> None:
    principal = Principal(subject="u1")
    assert principal.scopes == []
    assert principal.claims == {}
    request = AuthRequest()
    assert request.headers == {}
    assert request.query_params == {}
    verdict = GuardVerdict(allowed=False, reason="敏感词")
    assert verdict.sanitized_text is None
