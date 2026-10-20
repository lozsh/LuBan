"""yaml_io 映射与往返无损：契约 D1-8 决策 1 与验收标准。"""

from typing import Any

import pytest

from lubanagent.cli.yaml_io import (
    definition_from_yaml,
    definition_to_yaml,
    dump_yaml_file,
    load_yaml_file,
)
from lubanagent.core import AgentDefinition, BrainProfile, ErrorCode, LubanError

MINIMAL: dict[str, Any] = {"name": "a", "instruction": "你是助手"}

FULL: dict[str, Any] = {
    "name": "customer-service-bot",
    "description": "售后客服",
    "instruction": "你是售后客服，先安抚情绪，再查订单",
    "greeting": "你好，我是售后客服小鲁",
    "avatar": "avatar.svg",
    "brain": "smart",
    "fallbacks": ["fast", "cheap"],
    "tools": ["query_order", "refund_apply"],
    "mcp_servers": ["github-mcp"],
    "skills": ["refund-sop"],
    "knowledge": ["product-faq"],
    "memory": "session-summary",
    "limits": {
        "max_steps": 20,
        "budget_tokens": 200000,
        "budget_cost": 1.5,
        "timeout_sec": 120,
    },
    "io_contract": {"input": "text", "output": "text"},
}


def _strip(definition: AgentDefinition) -> dict[str, Any]:
    """status/version 不参与 YAML 往返（库内治理字段）。"""
    payload = definition.model_dump(mode="json")
    payload.pop("status")
    payload.pop("version")
    return payload


def test_from_yaml_full_mapping() -> None:
    definition = definition_from_yaml(FULL)
    assert definition.key == "customer-service-bot"
    assert definition.name == "customer-service-bot"
    assert definition.description == "售后客服"
    assert definition.instructions == "你是售后客服，先安抚情绪，再查订单"
    assert definition.greeting == "你好，我是售后客服小鲁"
    assert definition.avatar == "avatar.svg"
    assert definition.brain.profile is BrainProfile.SMART
    assert definition.brain.fallback_profiles == [BrainProfile.FAST, BrainProfile.CHEAP]
    assert definition.tools == ["query_order", "refund_apply"]
    assert definition.mcp_servers == ["github-mcp"]
    assert definition.skills == ["refund-sop"]
    assert definition.knowledge.sources == ["product-faq"]
    assert definition.memory.session is True
    assert definition.memory.summary is True
    assert definition.boundary.max_steps == 20
    assert definition.boundary.budget is not None
    assert definition.boundary.budget.max_tokens == 200000
    assert definition.boundary.budget.max_cost == 1.5
    assert definition.boundary.timeout_seconds == 120.0
    assert definition.io.input_type == "text"
    assert definition.io.output_schema is None


def test_from_yaml_minimal_defaults() -> None:
    definition = definition_from_yaml(MINIMAL)
    assert definition.brain.profile is BrainProfile.SMART
    assert definition.tools == []
    assert definition.mcp_servers == []
    assert definition.knowledge.sources == []
    assert definition.memory.session is True
    assert definition.memory.summary is False
    assert definition.boundary.max_steps == 16
    assert definition.boundary.budget is None
    assert definition.io.output_schema is None


def test_memory_variants() -> None:
    none = definition_from_yaml({**MINIMAL, "memory": "none"}).memory
    assert none.session is False and none.summary is False
    session = definition_from_yaml({**MINIMAL, "memory": "session"}).memory
    assert session.session is True and session.summary is False
    summary = definition_from_yaml({**MINIMAL, "memory": "session-summary"}).memory
    assert summary.session is True and summary.summary is True
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({**MINIMAL, "memory": "bogus"})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_io_output_schema_dict() -> None:
    definition = definition_from_yaml(
        {**MINIMAL, "io_contract": {"input": "json", "output": {"type": "object"}}}
    )
    assert definition.io.input_type == "json"
    assert definition.io.output_schema == {"type": "object"}
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({**MINIMAL, "io_contract": {"input": "text", "output": 42}})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_unknown_top_level_key() -> None:
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({**MINIMAL, "bogus": 1})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR
    assert exc.value.details["unknown_keys"] == ["bogus"]


def test_unknown_nested_key() -> None:
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({**MINIMAL, "limits": {"max_steps": 5, "bogus": 1}})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR
    assert exc.value.details["unknown_keys"] == ["limits.bogus"]


def test_missing_name_and_instruction() -> None:
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({"instruction": "i"})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({"name": "a"})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_invalid_brain() -> None:
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({**MINIMAL, "brain": "genius"})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(LubanError) as exc:
        definition_from_yaml({**MINIMAL, "fallbacks": ["genius"]})
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_round_trip_full_lossless() -> None:
    definition = definition_from_yaml(FULL)
    restored = definition_from_yaml(definition_to_yaml(definition))
    assert _strip(restored) == _strip(definition)


def test_round_trip_minimal_lossless_and_empty_omitted() -> None:
    definition = definition_from_yaml(MINIMAL)
    data = definition_to_yaml(definition)
    for key in ("description", "fallbacks", "tools", "mcp_servers", "skills", "knowledge"):
        assert key not in data
    restored = definition_from_yaml(data)
    assert _strip(restored) == _strip(definition)


def test_load_yaml_file_not_found(tmp_path) -> None:
    with pytest.raises(LubanError) as exc:
        load_yaml_file(tmp_path / "nope.yaml")
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_load_yaml_file_syntax_error(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(LubanError) as exc:
        load_yaml_file(bad)
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_dump_and_load_round_trip(tmp_path) -> None:
    path = tmp_path / "agent.yaml"
    dump_yaml_file(FULL, path)
    assert load_yaml_file(path) == FULL
    # allow_unicode：中文不转义
    assert "售后客服" in path.read_text(encoding="utf-8")


# ------------------------------------------------------------ knowledge dict 形态（防错答守门）


def test_from_yaml_knowledge_dict_guard_fields() -> None:
    """dict 形态：sources/top_k/cite/score_threshold/no_hit_fallback/max_age_days 全解析。"""
    definition = definition_from_yaml(
        {
            **MINIMAL,
            "knowledge": {
                "sources": ["product-faq"],
                "top_k": 3,
                "cite": False,
                "score_threshold": 0.2,
                "no_hit_fallback": "知识库未覆盖该问题。",
                "max_age_days": 30,
            },
        }
    )
    assert definition.knowledge.sources == ["product-faq"]
    assert definition.knowledge.top_k == 3
    assert definition.knowledge.cite is False
    assert definition.knowledge.score_threshold == 0.2
    assert definition.knowledge.no_hit_fallback == "知识库未覆盖该问题。"
    assert definition.knowledge.max_age_days == 30


def test_knowledge_list_shorthand_backward_compatible() -> None:
    """旧 list shorthand 等价于只设 sources（存量 YAML 零行为变化）。"""
    legacy = definition_from_yaml({**MINIMAL, "knowledge": ["faq"]})
    explicit = definition_from_yaml({**MINIMAL, "knowledge": {"sources": ["faq"]}})
    assert legacy.knowledge == explicit.knowledge
    assert legacy.knowledge.score_threshold == 0.0
    assert legacy.knowledge.no_hit_fallback is None
    assert legacy.knowledge.max_age_days is None


def test_knowledge_round_trip_dict_and_list() -> None:
    """守门字段非默认 → 导出 dict；全默认只有 sources → 导出 list（往返兼容）。"""
    guarded = definition_from_yaml(
        {
            **MINIMAL,
            "knowledge": {
                "sources": ["faq"],
                "score_threshold": 0.2,
                "no_hit_fallback": "未覆盖。",
            },
        }
    )
    data = definition_to_yaml(guarded)
    assert data["knowledge"] == {
        "sources": ["faq"],
        "score_threshold": 0.2,
        "no_hit_fallback": "未覆盖。",
    }
    assert definition_from_yaml(data).knowledge == guarded.knowledge

    plain = definition_from_yaml({**MINIMAL, "knowledge": ["faq"]})
    assert definition_to_yaml(plain)["knowledge"] == ["faq"]
    assert definition_from_yaml(definition_to_yaml(plain)).knowledge == plain.knowledge


def test_knowledge_unknown_key_rejected() -> None:
    """dict 形态未知键显式报错（与其他嵌套段一致）。"""
    with pytest.raises(LubanError) as exc_info:
        definition_from_yaml({**MINIMAL, "knowledge": {"sources": [], "threshold": 0.1}})
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    assert "knowledge.threshold" in str(exc_info.value.message)
