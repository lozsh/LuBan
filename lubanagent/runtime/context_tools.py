"""上下文引用取回伴生工具：knowledge_read / memory_read（链式召回的读回端）。

仅在对应注入预算启用时由 capabilities 装配（Agent 定义即门控）；
越权一律返回 not_found（不泄露存在性）。命名冲突由调用方按 luban: 前缀惯例改名。
"""

from typing import Any

from lubanagent.core import RiskLevel, ToolDescriptor
from lubanagent.extension import RegisteredTool
from lubanagent.knowledge import KnowledgeStore
from lubanagent.memory import LongTermMemory
from lubanagent.persistence import Database

_NOT_FOUND: dict[str, Any] = {"error": "not_found"}


def make_knowledge_read_tool(
    store: KnowledgeStore, allowed_sources: list[str]
) -> RegisteredTool:
    """按 ref_id（"kn:12" 或裸 "12"）取知识 chunk 全文；source 不在 allowed_sources 视同不存在。"""

    async def handler(ref_id: str) -> dict[str, Any]:
        raw = ref_id.split(":", 1)[-1].strip()
        try:
            chunk_id = int(raw)
        except ValueError:
            return dict(_NOT_FOUND)
        chunk = await store.get_chunk(chunk_id)
        if chunk is None or chunk["source"] not in allowed_sources:
            return dict(_NOT_FOUND)
        return {
            "ref_id": f"kn:{chunk['id']}",
            "source": chunk["source"],
            "chunk_index": chunk["chunk_index"],
            "content": chunk["content"],
        }

    descriptor = ToolDescriptor(
        key="knowledge_read",
        name="knowledge_read",
        description="按引用 id 取回知识片段全文（上下文里仅有摘要时用）",
        args_schema={
            "type": "object",
            "properties": {"ref_id": {"type": "string", "description": "如 kn:12"}},
            "required": ["ref_id"],
        },
        risk=RiskLevel.LOW,
    )
    return RegisteredTool(descriptor=descriptor, handler=handler)


def make_memory_read_tool(
    db: Database, agent_key: str, user_key: str
) -> RegisteredTool:
    """按 ref_id（"mem:3" 或裸 "3"）取长期记忆条目全文；限定 agent+user 防越权。"""

    async def handler(ref_id: str) -> dict[str, Any]:
        raw = ref_id.split(":", 1)[-1].strip()
        try:
            fact_id = int(raw)
        except ValueError:
            return dict(_NOT_FOUND)
        fact = await LongTermMemory(db).get(agent_key, user_key, fact_id)
        if fact is None:
            return dict(_NOT_FOUND)
        return {"ref_id": f"mem:{fact_id}", "content": fact}

    descriptor = ToolDescriptor(
        key="memory_read",
        name="memory_read",
        description="按引用 id 取回用户信息条目全文（上下文里仅有摘要时用）",
        args_schema={
            "type": "object",
            "properties": {"ref_id": {"type": "string", "description": "如 mem:3"}},
            "required": ["ref_id"],
        },
        risk=RiskLevel.LOW,
    )
    return RegisteredTool(descriptor=descriptor, handler=handler)


__all__ = ["make_knowledge_read_tool", "make_memory_read_tool"]
