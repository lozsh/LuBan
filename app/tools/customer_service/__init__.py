"""客服示例工具包：订单查询 + 退款申请（@tool 装饰即注册）。

import 本包即触发装饰器注册（app.tools 是约定扫描入口，见 config/defaults.py）。
"""

from . import order, refund  # 导入即注册

__all__ = ["order", "refund"]
