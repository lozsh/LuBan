"""/v1/watchdog 路由：Watchdog 告警查询（只读；服务未启用返回空列表）。"""

from fastapi import APIRouter, Request

from lubanagent.watchdog import WatchdogAlert

router = APIRouter(tags=["watchdog"])


@router.get("/watchdog/alerts", response_model=list[WatchdogAlert])
async def list_alerts(request: Request) -> list[WatchdogAlert]:
    """最近告警留痕（进程内 deque，重启清零）；WATCHDOG_ENABLED=false 时恒为空。"""
    service = getattr(request.app.state, "watchdog", None)
    if service is None:
        return []
    return service.recent_alerts()
