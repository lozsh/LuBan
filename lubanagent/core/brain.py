"""逻辑大脑槽位: fast/smart/cheap/vision 四档解耦 Agent 与具体模型。"""

from enum import Enum


class BrainProfile(str, Enum):  # noqa: UP042  # 契约钉死枚举用 str, Enum
    """大脑槽位, 切换模型 = 改库映射, 不改代码。"""

    FAST = "fast"
    SMART = "smart"
    CHEAP = "cheap"
    VISION = "vision"
