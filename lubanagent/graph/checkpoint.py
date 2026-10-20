"""LubanCheckpointer re-export 垫片：实现已下沉 `lubanagent.persistence.checkpoint`。

下沉原因（缺口 #2 契约）：普通 Agent Run 复用同一 Saver，而分层契约禁止
runtime → graph 依赖（runtime 自闭环），persistence 是两者共同的下层。
本垫片保持 `lubanagent.graph.checkpoint` / `lubanagent.graph.LubanCheckpointer`
旧导入路径零破坏。
"""

from lubanagent.persistence.checkpoint import LubanCheckpointer

__all__ = ["LubanCheckpointer"]
