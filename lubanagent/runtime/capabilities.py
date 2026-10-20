"""能力接线：RAG / 记忆 / Skill 按 Agent 定义装配进运行主流程。

agent_loop 在「上下文组装前」调 prepare、在「Run 收尾后」调 finalize；
全部能力默认关闭——未配置对应字段时两函数零副作用（显式 early-return）。
KV-cache 语义：知识片段 / 长期记忆 / Skill 元数据统一注入 system prompt 尾部
（动态后置、稳定前缀不变）；摘要记忆的摘要前缀由 SummaryMemory.context_prefix 管理。
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import BaseMessage

from lubanagent.core import AgentDefinition, ErrorCode, LubanError
from lubanagent.extension import RegisteredTool, Registry, Snippet
from lubanagent.knowledge import format_snippets_for_prompt
from lubanagent.memory import (
    LongTermMemory,
    SessionMemory,
    Summarizer,
    SummaryMemory,
    format_facts_for_prompt,
)
from lubanagent.persistence import Database
from lubanagent.skill import SkillRegistry

logger = logging.getLogger(__name__)

_KNOWLEDGE_HEADER_CITE = (
    "以下来自知识库的检索片段。回答规则：只允许基于以下编号片段作答，"
    "答案必须能从片段得出；无法从片段得出时必须直接回答不知道，"
    "禁止推测或补充片段外知识；引用来源时使用 [n] 编号："
)
_KNOWLEDGE_HEADER_PLAIN = "以下来自知识库的检索片段："
_SKILL_HEADER = "可用 Skills（需要时先用 skill_load 工具加载正文再执行）："


def _filter_snippets(snippets: list[Snippet], config: Any) -> list[Snippet]:
    """按 score_threshold 与 max_age_days 过滤（代码强制守门）。

    score 低于阈值丢弃；metadata.ingested_at 超龄丢弃（无 ingested_at 或解析失败保留，
    兼容存量数据）。两项全默认时原样返回（零行为变化）。
    """
    if config.score_threshold > 0.0:
        snippets = [s for s in snippets if s.score >= config.score_threshold]
    if config.max_age_days is not None:
        deadline = datetime.now(UTC) - timedelta(days=config.max_age_days)
        kept: list[Snippet] = []
        for snippet in snippets:
            stamp = snippet.metadata.get("ingested_at")
            if not isinstance(stamp, str):
                kept.append(snippet)  # 无时间戳 → 保留（存量兼容）
                continue
            try:
                ingested = datetime.fromisoformat(stamp)
            except ValueError:
                kept.append(snippet)  # 非法格式 → 保留，不因脏数据误杀
                continue
            if ingested.tzinfo is None:
                ingested = ingested.replace(tzinfo=UTC)
            if ingested >= deadline:
                kept.append(snippet)
        snippets = kept
    return snippets


@dataclass(frozen=True)
class CapabilityContext:
    """prepare 的产出：供 agent_loop 注入上下文与工具的增量。"""

    system_suffix: str = ""  # 追加到 system prompt 尾部（长期记忆/知识片段/Skill 元数据，按此序）
    # skill_load（agent.skills 非空时）
    extra_tools: list[RegisteredTool] = field(default_factory=list)
    history: list[BaseMessage] | None = None  # 会话/摘要记忆提供的历史（None = 不启用记忆）
    # 知识检索全 miss（阈值/超龄过滤后为空且配置了 no_hit_fallback）：
    # agent_loop 据此短路拒答（不调模型，output = no_hit_fallback）
    knowledge_no_hit: bool = False
    # 守门观测（阈值校准排障用）：检索原始命中数 / 过滤后保留数
    knowledge_hits_raw: int = 0
    knowledge_hits_kept: int = 0


async def prepare(
    definition: AgentDefinition,
    *,
    user_input: str,
    session_id: str | None,
    user_key: str | None,
    db: Database,
    registry: Registry,
    skill_registry: SkillRegistry | None = None,
    summarizer: Summarizer | None = None,
) -> CapabilityContext:
    """按 definition 配置逐项装配（全部代码强制开关；全关时零读写零副作用）。

    1. memory.session 或 memory.summary：经 SummaryMemory.context_prefix（summary=True 且配置了
       summarizer）或 SessionMemory.history（仅 session=True）→ history；session_id 为 None → 跳过
    2. memory.long_term 且 user_key：LongTermMemory.recall → format_facts_for_prompt 入 suffix
    3. knowledge.sources 非空：逐 source 名 retrieve(user_input, top_k) → 按 score_threshold/
       max_age_days 过滤 → cite=True 带编号与强引用约束 / False 纯片段入 suffix；
       connector 未注册 → LubanError(CONFIG_ERROR)（显式失败）；
       过滤后为空且配置 no_hit_fallback → knowledge_no_hit=True（agent_loop 短路拒答）
    4. skills 非空且给了 skill_registry：format_metadata_for_prompt 入 suffix
       + make_load_tool(allowed=definition.skills) 入 extra_tools
    """
    memory_cfg = definition.memory
    # 能力缺失显式失败（H09）：能力开启但装配依赖缺失 → CONFIG_ERROR，不静默"看似开启"
    if definition.skills and skill_registry is None:
        raise LubanError(
            ErrorCode.CONFIG_ERROR,
            f"Agent {definition.key} 开启了 skills，但运行时未注入 SkillRegistry"
            "（部署者经 create_app(skill_registry=...) 或 AgentRuntime(skill_registry=...) 注入）",
        )
    if memory_cfg.long_term and not user_key:
        raise LubanError(
            ErrorCode.CONFIG_ERROR,
            f"Agent {definition.key} 开启了长期记忆，但运行时无可信 user_key 来源"
            "（部署者经 create_app(user_key_fn=...) 注入身份映射；客户端 user_id 不作为授权依据）",
        )
    # 显式开关判断：任何一项启用才继续，全关走 early-return（契约行为 1）
    use_history = (memory_cfg.session or memory_cfg.summary) and session_id is not None
    use_long_term = memory_cfg.long_term and bool(user_key)
    use_knowledge = bool(definition.knowledge.sources)
    use_skills = bool(definition.skills)
    if not (use_history or use_long_term or use_knowledge or use_skills):
        return CapabilityContext()

    # 1. 会话/摘要记忆 → history（覆盖 request.history，行为可预期）
    history: list[BaseMessage] | None = None
    if use_history and session_id is not None:
        if memory_cfg.summary and summarizer is not None:
            history = await SummaryMemory(db, summarizer=summarizer).context_prefix(
                session_id, window_size=memory_cfg.window_size
            )
        else:
            if memory_cfg.summary:
                logger.warning(
                    "Agent %s 开启摘要记忆但未配置 summarizer，退化为会话记忆",
                    definition.key,
                )
            history = await SessionMemory(db).history(
                session_id, window_size=memory_cfg.window_size
            )

    suffix_parts: list[str] = []
    # 2. 长期记忆 → suffix
    if use_long_term and user_key:
        facts = await LongTermMemory(db).recall(definition.key, user_key)
        facts_text = format_facts_for_prompt(facts)
        if facts_text:
            suffix_parts.append(facts_text)

    # 3. 知识检索 → suffix（query 用当前 user_input，v1 不做多轮改写）
    knowledge_no_hit = False
    knowledge_hits_raw = 0
    knowledge_hits_kept = 0
    if use_knowledge:
        snippets = []
        for name in definition.knowledge.sources:
            if name not in registry.list_knowledge():
                raise LubanError(
                    ErrorCode.CONFIG_ERROR,
                    f"Agent {definition.key} 引用的知识源未注册: {name}",
                    details={"source": name},
                )
            connector = registry.get_knowledge(name)
            snippets.extend(
                await connector.retrieve(user_input, top_k=definition.knowledge.top_k)
            )
        knowledge_hits_raw = len(snippets)
        # 防错答守门（代码强制）：弱相关/超龄片段丢弃，宁缺毋滥
        snippets = _filter_snippets(snippets, definition.knowledge)
        knowledge_hits_kept = len(snippets)
        if snippets:
            if definition.knowledge.cite:
                suffix_parts.append(
                    f"{_KNOWLEDGE_HEADER_CITE}\n{format_snippets_for_prompt(snippets)}"
                )
            else:
                body = "\n".join(snippet.content for snippet in snippets)
                suffix_parts.append(f"{_KNOWLEDGE_HEADER_PLAIN}\n{body}")
        elif definition.knowledge.no_hit_fallback is not None:
            # 全 miss 且配置了兜底话术 → 标记短路（agent_loop 不调模型直接拒答）
            knowledge_no_hit = True

    # 4. Skill 元数据 → suffix；skill_load 工具 → extra_tools
    extra_tools: list[RegisteredTool] = []
    if use_skills and skill_registry is not None:
        metadata = skill_registry.format_metadata_for_prompt(definition.skills)
        if metadata:
            suffix_parts.append(f"{_SKILL_HEADER}\n{metadata}")
        load_tool = skill_registry.make_load_tool(allowed=definition.skills)
        key = load_tool.descriptor.key
        if registry.has_tool(key):
            # 与用户注册的同名工具冲突：框架版改名 luban:skill_load 并记 warning（契约决策 3）
            renamed = f"luban:{key}"
            logger.warning(
                "工具 %s 与用户注册工具冲突，框架 Skill 加载工具改名为 %s", key, renamed
            )
            descriptor = load_tool.descriptor.model_copy(
                update={"key": renamed, "name": renamed}
            )
            load_tool = RegisteredTool(descriptor=descriptor, handler=load_tool.handler)
        extra_tools.append(load_tool)

    return CapabilityContext(
        system_suffix="\n\n".join(suffix_parts),
        extra_tools=extra_tools,
        history=history,
        knowledge_no_hit=knowledge_no_hit,
        knowledge_hits_raw=knowledge_hits_raw,
        knowledge_hits_kept=knowledge_hits_kept,
    )


async def finalize(
    definition: AgentDefinition,
    *,
    session_id: str | None,
    run_id: str,
    user_input: str,
    output: str | None,
    db: Database,
    summarizer: Summarizer | None = None,
) -> None:
    """Run 收尾回写（仅 memory.session/summary 且 session_id 给了时）。

    先 append(user) 后 append(assistant)（run_id 都传）；memory.summary=True 时再
    SummaryMemory.maybe_compress。回写失败只记日志不阻断（Run 已终态，记忆是附属）。
    """
    memory_cfg = definition.memory
    if not (memory_cfg.session or memory_cfg.summary) or session_id is None:
        return
    try:
        memory = SessionMemory(db)
        await memory.append(session_id, "user", user_input, run_id=run_id)
        if output is not None:
            await memory.append(session_id, "assistant", output, run_id=run_id)
        if memory_cfg.summary:
            if summarizer is None:
                logger.warning(
                    "Agent %s 开启摘要记忆但未配置 summarizer，跳过压缩", definition.key
                )
            else:
                await SummaryMemory(db, summarizer=summarizer).maybe_compress(
                    session_id, window_size=memory_cfg.window_size
                )
    except Exception:
        logger.exception("Run %s 记忆回写失败（不阻断 Run）", run_id)


__all__ = ["CapabilityContext", "finalize", "prepare"]
