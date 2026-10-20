"""builder 校验：无 entry / 边引用不存在节点 / 孤立节点 → VALIDATION_ERROR。"""

import pytest

from lubanagent.core import ErrorCode, LubanError
from lubanagent.graph import GraphBuilder


def test_build_without_entry_fails():
    builder = GraphBuilder("g").step("a", lambda s: s)
    with pytest.raises(LubanError) as exc_info:
        builder.build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_entry_referencing_missing_node_fails():
    builder = GraphBuilder("g").step("a", lambda s: s).entry("ghost")
    with pytest.raises(LubanError) as exc_info:
        builder.build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_edge_referencing_missing_node_fails():
    builder = GraphBuilder("g").step("a", lambda s: s).entry("a").edge("a", "ghost")
    with pytest.raises(LubanError) as exc_info:
        builder.build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_conditional_edge_referencing_missing_node_fails():
    builder = (
        GraphBuilder("g")
        .step("a", lambda s: s)
        .entry("a")
        .when("a", lambda s: True, then="ghost", otherwise="a")
    )
    with pytest.raises(LubanError) as exc_info:
        builder.build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_orphan_node_fails():
    builder = (
        GraphBuilder("g")
        .step("a", lambda s: s)
        .step("orphan", lambda s: s)
        .entry("a")
    )
    with pytest.raises(LubanError) as exc_info:
        builder.build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_duplicate_node_fails():
    builder = GraphBuilder("g").step("a", lambda s: s)
    with pytest.raises(LubanError) as exc_info:
        builder.step("a", lambda s: s)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_empty_graph_fails():
    with pytest.raises(LubanError) as exc_info:
        GraphBuilder("g").build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_to_graph_descriptor():
    flow = GraphBuilder("g", description="演示").step("a", lambda s: s).entry("a").build()
    descriptor = flow.to_graph_descriptor()
    assert descriptor.name == "g"
    assert descriptor.description == "演示"
    assert callable(descriptor.builder)
