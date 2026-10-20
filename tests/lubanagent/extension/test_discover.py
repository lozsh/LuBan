"""discover 行为: 自动发现注册、返回值计数、重复调用幂等、发现后函数可调。"""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from lubanagent.extension import default_registry, discover

SANDBOX = str(Path(__file__).parent / "_sandbox")


@pytest.fixture
def sandbox_path() -> Iterator[None]:
    sys.path.insert(0, SANDBOX)
    yield
    sys.path.remove(SANDBOX)


def test_discover_registers_and_idempotent(sandbox_path: None) -> None:
    first = discover(["fakepkg"])
    assert first >= 3  # tool + graph + hook
    assert default_registry.has_tool("fake.echo")
    assert default_registry.get_graph("fake.flow").name == "fake.flow"
    assert any(type(h).__name__ == "FakeHook" for h in default_registry.hooks())

    second = discover(["fakepkg"])
    assert second == 0


def test_discovered_tool_still_callable(sandbox_path: None) -> None:
    discover(["fakepkg"])
    registered = default_registry.get_tool("fake.echo")
    assert registered.handler("ab", count=2) == "abab"
    assert registered.descriptor.contract.approval_required is True
