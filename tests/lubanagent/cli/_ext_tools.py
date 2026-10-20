"""eval CLI 扩展扫描回归测试的专用工具包（经 EXTENSION_PACKAGES 环境变量扫描）。

不走 app.tools（框架测试不依赖用户领地），用最小 @tool 验证「独立 CLI 进程里
扩展包被扫描、带工具的 Agent 可运行」——修复前该场景 100% 报「工具已注销」。
"""

from pathlib import Path

from lubanagent.core import RiskLevel
from lubanagent.extension import tool


@tool(
    key="eval_ext_lookup",
    description="eval CLI 扩展扫描回归测试用：返回入参原样。",
    risk=RiskLevel.LOW,
    timeout_seconds=5.0,
    idempotent=True,
)
def eval_ext_lookup(code: str) -> dict:
    """按编码查询（测试桩）。"""
    return {"code": code, "found": True}


@tool(
    key="eval_ext_risky",
    description="eval CLI 审批放行回归测试用：高风险审批型工具，执行时写标记文件。",
    risk=RiskLevel.HIGH,
    timeout_seconds=5.0,
    idempotent=False,
    approval_required=True,
)
def eval_ext_risky(target: str) -> dict:
    """执行高风险操作（测试桩）：向 target 路径写标记文件，验证是否真实执行。"""
    Path(target).write_text("executed", encoding="utf-8")
    return {"target": target, "done": True}
