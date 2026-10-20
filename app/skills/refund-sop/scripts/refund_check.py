#!/usr/bin/env python3
"""退款前置校验脚本：校验订单号格式（SKILL 演示脚本，受控执行沙箱可跑）。

用法：python refund_check.py <order_id>
返回：exit 0 + JSON stdout（合法）或 exit 1 + 错误信息（非法）。
"""

import json
import re
import sys

# 订单号格式：ORD-开头 + 4 位数字
_ORDER_RE = re.compile(r"^ORD-\d{4}$")


def check(order_id: str) -> dict:
    """校验订单号格式，返回结果字典。"""
    if not order_id:
        return {"valid": False, "reason": "订单号为空"}
    if not _ORDER_RE.match(order_id):
        return {
            "valid": False,
            "reason": f"订单号格式非法（应为 ORD-XXXX）：{order_id}",
        }
    return {"valid": True, "order_id": order_id}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"valid": False, "reason": "缺少参数：order_id"}, ensure_ascii=False))
        sys.exit(1)
    result = check(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
