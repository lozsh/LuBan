# 第 4 课参考产出：app/tools/my_library/ 包（两个文件 + __init__.py）
# 文件一 lookup.py（低风险只读）：

from lubanagent.core import RiskLevel
from lubanagent.extension import tool

# 教程演示用内置数据；真实场景换成 HTTP 调用你的业务系统，
# 模式照抄 app/tools/customer_service/order.py。
_BORROWS: dict[str, list[dict]] = {
    "2023011234": [
        {"title": "三体", "due": "2026-09-20", "renewals_left": 1},
        {"title": "机器学习系统设计", "due": "2026-09-12", "renewals_left": 0},
    ],
}
# 第 7 课步骤 2 会把 _BORROWS 换成含逾期案例的版本（日期改早 + 加一个轻度逾期读者），见 07 课文。


@tool(
    key="my_lib_lookup",
    description="按学号查询借阅记录（书名/应还日期/剩余续借次数）。",
    risk=RiskLevel.LOW,
    timeout_seconds=10.0,
    idempotent=True,
)
def my_lib_lookup(student_id: str) -> dict:
    """查借阅记录。

    参数: student_id: 学号（如 2023011234）
    """
    records = _BORROWS.get(student_id, [])
    return {"student_id": student_id, "count": len(records), "records": records}


# 文件二 report_loss.py（高风险 + 审批）：

CARD_LOSS_FEE = 20.0  # 挂失补办工本费（元）


@tool(
    key="my_lib_report_loss",
    description=f"提交校园卡挂失申请（高风险：收工本费 {CARD_LOSS_FEE:g} 元，需人工审批）。",
    risk=RiskLevel.HIGH,
    timeout_seconds=15.0,
    idempotent=False,
    approval_required=True,
)
def my_lib_report_loss(student_id: str) -> dict:
    """挂失校园卡并生成补办申请。

    参数: student_id: 学号（如 2023011234）
    """
    return {
        "student_id": student_id,
        "status": "loss_reported",
        "fee": CARD_LOSS_FEE,
        "pickup": "三日后凭学生证到图书馆前台领新卡",
    }


# 文件三 __init__.py（导入即注册）：
#     from . import lookup, report_loss  # 导入即注册
#
# Agent YAML 的 tools 段（本课结束时）：
#     tools: [my_lib_lookup, my_lib_report_loss]
