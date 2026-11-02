"""外部 Watchdog：主系统外的定时控制回路，判 RUNNING Run 是否有实质进展。

默认关闭（WATCHDOG_ENABLED=false）；开启后由装配根（app/factory lifespan）起巡检 task。
本包只读 persistence + 发 WATCHDOG_ALERT 事件，不 import runtime（取消动作经注入回调）。
"""

from lubanagent.watchdog.service import (
    AlertOnlyHandler,
    CancelHandler,
    WatchdogActionHandler,
    WatchdogAlert,
    WatchdogService,
)

__all__ = [
    "AlertOnlyHandler",
    "CancelHandler",
    "WatchdogActionHandler",
    "WatchdogAlert",
    "WatchdogService",
]
