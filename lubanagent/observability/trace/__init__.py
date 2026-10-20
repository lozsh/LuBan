"""trace：内部 DB 双写主笔，always on，不依赖任何外部后端。"""

from lubanagent.observability.trace.writer import TraceWriter

__all__ = ["TraceWriter"]
