"""yaml_io：Agent 定义 YAML shorthand 与 AgentDefinition 的双向映射（契约 D1-8 决策 1）。

YAML 仅用于初始化导入 / 导出迁移，不是存储；本模块只做映射，不复制 core 字段逻辑。
"""

from pathlib import Path
from typing import Any

import yaml

from lubanagent.core import (
    AgentDefinition,
    BoundaryConfig,
    BrainProfile,
    Budget,
    ErrorCode,
    IOContract,
    KnowledgeConfig,
    LubanError,
    MemoryConfig,
)

_TOP_LEVEL_KEYS = {
    "name",
    "description",
    "instruction",
    "greeting",
    "avatar",
    "brain",
    "fallbacks",
    "tools",
    "mcp_servers",
    "skills",
    "knowledge",
    "memory",
    "limits",
    "io_contract",
}
_LIMITS_KEYS = {"max_steps", "budget_tokens", "budget_cost", "timeout_sec"}
_IO_KEYS = {"input", "output"}
_MEMORY_VALUES = ("session", "session-summary", "none")
_KNOWLEDGE_KEYS = {
    "sources",
    "top_k",
    "cite",
    "score_threshold",
    "no_hit_fallback",
    "max_age_days",
}


def _error(message: str, *, details: dict[str, Any] | None = None) -> LubanError:
    return LubanError(ErrorCode.VALIDATION_ERROR, message, details=details)


def _check_unknown(data: dict[str, Any], allowed: set[str], *, prefix: str = "") -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        keys = [f"{prefix}{key}" for key in unknown]
        raise _error(f"未知 YAML 键: {keys}", details={"unknown_keys": keys})


def _parse_brain(value: Any, *, field: str) -> BrainProfile:
    try:
        return BrainProfile(value)
    except ValueError:
        valid = [profile.value for profile in BrainProfile]
        raise _error(
            f"非法 brain 槽位: {value!r}（{field}），可选: {valid}",
            details={"field": field, "value": value},
        ) from None


def _parse_memory(value: Any) -> MemoryConfig:
    if value == "session":
        return MemoryConfig(session=True, summary=False)
    if value == "session-summary":
        return MemoryConfig(session=True, summary=True)
    if value == "none":
        return MemoryConfig(session=False, summary=False)
    raise _error(
        f"非法 memory 值: {value!r}，可选: {list(_MEMORY_VALUES)}",
        details={"field": "memory", "value": value},
    )


def _parse_limits(value: Any) -> BoundaryConfig:
    if not isinstance(value, dict):
        raise _error("limits 必须是映射", details={"field": "limits"})
    _check_unknown(value, _LIMITS_KEYS, prefix="limits.")
    boundary = BoundaryConfig()
    if "max_steps" in value:
        boundary.max_steps = int(value["max_steps"])
    if "timeout_sec" in value:
        boundary.timeout_seconds = float(value["timeout_sec"])
    budget_tokens = value.get("budget_tokens")
    budget_cost = value.get("budget_cost")
    if budget_tokens is not None or budget_cost is not None:
        boundary.budget = Budget(
            max_tokens=int(budget_tokens) if budget_tokens is not None else None,
            max_cost=float(budget_cost) if budget_cost is not None else None,
        )
    return boundary


def _parse_knowledge(value: Any) -> KnowledgeConfig:
    """两种形态：list shorthand（只设 sources）与 dict（全字段，防错答守门配置）。"""
    if isinstance(value, list):  # 旧 shorthand：knowledge: [product-faq]
        return KnowledgeConfig(sources=_str_list(value, field="knowledge"))
    if not isinstance(value, dict):
        raise _error(
            "knowledge 必须是来源列表或映射", details={"field": "knowledge", "value": value}
        )
    _check_unknown(value, _KNOWLEDGE_KEYS, prefix="knowledge.")
    config = KnowledgeConfig()
    if "sources" in value:
        config.sources = _str_list(value["sources"], field="knowledge.sources")
    if "top_k" in value:
        config.top_k = int(value["top_k"])
    if "cite" in value:
        config.cite = bool(value["cite"])
    if "score_threshold" in value:
        config.score_threshold = float(value["score_threshold"])
    if "no_hit_fallback" in value:
        fallback = value["no_hit_fallback"]
        if fallback is not None and not isinstance(fallback, str):
            raise _error(
                "knowledge.no_hit_fallback 必须是字符串",
                details={"field": "knowledge.no_hit_fallback", "value": fallback},
            )
        config.no_hit_fallback = fallback
    if "max_age_days" in value:
        age = value["max_age_days"]
        config.max_age_days = int(age) if age is not None else None
    return config


def _parse_io(value: Any) -> IOContract:
    if not isinstance(value, dict):
        raise _error("io_contract 必须是映射", details={"field": "io_contract"})
    _check_unknown(value, _IO_KEYS, prefix="io_contract.")
    contract = IOContract()
    if "input" in value:
        input_type = value["input"]
        if input_type not in ("text", "json", "multimodal"):
            raise _error(
                f"非法 io_contract.input: {input_type!r}",
                details={"field": "io_contract.input", "value": input_type},
            )
        contract.input_type = input_type
    if "output" in value:
        output = value["output"]
        if output is None or output == "text":
            contract.output_schema = None
        elif isinstance(output, dict):
            contract.output_schema = output
        else:
            raise _error(
                f"非法 io_contract.output: {output!r}（\"text\" 或 schema 映射）",
                details={"field": "io_contract.output", "value": output},
            )
    return contract


def _str_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _error(f"{field} 必须是字符串列表", details={"field": field})
    return list(value)


def definition_from_yaml(data: dict[str, Any]) -> AgentDefinition:
    """按决策 1 映射 YAML shorthand → AgentDefinition。

    未知键 / 缺 name、instruction / brain 非法值 → VALIDATION_ERROR。
    """
    if not isinstance(data, dict):
        raise _error("YAML 顶层必须是映射")
    _check_unknown(data, _TOP_LEVEL_KEYS)
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise _error("缺少必填键: name")
    instruction = data.get("instruction")
    if not isinstance(instruction, str) or not instruction:
        raise _error("缺少必填键: instruction")

    definition = AgentDefinition(key=name, name=name)
    description = data.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise _error("description 必须是字符串", details={"field": "description"})
        definition.description = description
    definition.instructions = instruction
    greeting = data.get("greeting")
    if greeting is not None:
        if not isinstance(greeting, str):
            raise _error("greeting 必须是字符串", details={"field": "greeting"})
        definition.greeting = greeting
    avatar = data.get("avatar")
    if avatar is not None:
        if not isinstance(avatar, str):
            raise _error("avatar 必须是字符串（落盘文件名）", details={"field": "avatar"})
        definition.avatar = avatar
    if "brain" in data:
        definition.brain.profile = _parse_brain(data["brain"], field="brain")
    if "fallbacks" in data:
        definition.brain.fallback_profiles = [
            _parse_brain(item, field="fallbacks")
            for item in _str_list(data["fallbacks"], field="fallbacks")
        ]
    definition.tools = _str_list(data.get("tools"), field="tools")
    definition.mcp_servers = _str_list(data.get("mcp_servers"), field="mcp_servers")
    definition.skills = _str_list(data.get("skills"), field="skills")
    if "knowledge" in data:
        definition.knowledge = _parse_knowledge(data["knowledge"])
    if "memory" in data:
        definition.memory = _parse_memory(data["memory"])
    if "limits" in data:
        definition.boundary = _parse_limits(data["limits"])
    if "io_contract" in data:
        definition.io = _parse_io(data["io_contract"])
    return definition


def _memory_to_yaml(memory: MemoryConfig) -> str:
    if not memory.session and not memory.summary:
        return "none"
    # 会话摘要以会话记忆为前提（summary-only 归为 session-summary）
    if memory.summary:
        return "session-summary"
    return "session"


def _limits_to_yaml(boundary: BoundaryConfig) -> dict[str, Any]:
    limits: dict[str, Any] = {"max_steps": boundary.max_steps}
    if boundary.budget is not None:
        if boundary.budget.max_tokens is not None:
            limits["budget_tokens"] = boundary.budget.max_tokens
        if boundary.budget.max_cost is not None:
            limits["budget_cost"] = boundary.budget.max_cost
    limits["timeout_sec"] = boundary.timeout_seconds
    return limits


def _knowledge_to_yaml(config: KnowledgeConfig) -> Any:
    """仅 sources 非空且其余全默认 → list shorthand（往返兼容）；否则完整 dict。"""
    if (
        config.sources
        and config.top_k == KnowledgeConfig().top_k
        and config.cite
        and config.score_threshold == 0.0
        and config.no_hit_fallback is None
        and config.max_age_days is None
    ):
        return list(config.sources)
    data: dict[str, Any] = {}
    if config.sources:
        data["sources"] = list(config.sources)
    if config.top_k != KnowledgeConfig().top_k:
        data["top_k"] = config.top_k
    if not config.cite:
        data["cite"] = False
    if config.score_threshold != 0.0:
        data["score_threshold"] = config.score_threshold
    if config.no_hit_fallback is not None:
        data["no_hit_fallback"] = config.no_hit_fallback
    if config.max_age_days is not None:
        data["max_age_days"] = config.max_age_days
    return data


def definition_to_yaml(definition: AgentDefinition) -> dict[str, Any]:
    """同映射反向：AgentDefinition → YAML shorthand；空集合字段省略。"""
    data: dict[str, Any] = {"name": definition.key}
    if definition.description:
        data["description"] = definition.description
    if definition.greeting:
        data["greeting"] = definition.greeting
    if definition.avatar:
        data["avatar"] = definition.avatar
    data["instruction"] = definition.instructions
    data["brain"] = definition.brain.profile.value
    if definition.brain.fallback_profiles:
        data["fallbacks"] = [profile.value for profile in definition.brain.fallback_profiles]
    if definition.tools:
        data["tools"] = list(definition.tools)
    if definition.mcp_servers:
        data["mcp_servers"] = list(definition.mcp_servers)
    if definition.skills:
        data["skills"] = list(definition.skills)
    if definition.knowledge.sources or definition.knowledge != KnowledgeConfig():
        data["knowledge"] = _knowledge_to_yaml(definition.knowledge)
    data["memory"] = _memory_to_yaml(definition.memory)
    data["limits"] = _limits_to_yaml(definition.boundary)
    output: Any = (
        definition.io.output_schema if definition.io.output_schema is not None else "text"
    )
    data["io_contract"] = {"input": definition.io.input_type, "output": output}
    return data


def load_yaml_file(path: Path) -> dict[str, Any]:
    """yaml.safe_load；文件不存在/语法错/顶层非映射 → VALIDATION_ERROR。"""
    if not path.is_file():
        raise _error(f"YAML 文件不存在: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise _error(f"YAML 语法错误: {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise _error(f"YAML 顶层必须是映射: {path}")
    return data


def dump_yaml_file(data: dict[str, Any], path: Path) -> None:
    """safe_dump 落盘（allow_unicode、保持键序）。"""
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
