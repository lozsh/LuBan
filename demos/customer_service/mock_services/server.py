"""Mock 业务服务：订单/退款/产品库三域，纯 stdlib http.server，端口 9100。

零额外依赖，`python -m demos.customer_service.mock_services` 可起。
Tool 只经 HTTP 认 BIZ_BASE_URL，禁止 import 本模块（领地隔离）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_PORT = 9100
_LOCK = threading.Lock()


def _load(name: str) -> list | dict:
    return json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))


def _save(name: str, data: list | dict) -> None:
    (_DATA_DIR / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class _State:
    """进程内可变状态（线程安全靠 _LOCK）；退款回写 refunds.json。"""

    def __init__(self) -> None:
        self.orders = _load("orders.json")
        self.refunds = _load("refunds.json")
        self.products = _load("products.json")

    def reload(self) -> None:
        with _LOCK:
            self.orders = _load("orders.json")
            self.refunds = _load("refunds.json")
            self.products = _load("products.json")


_STATE = _State()


def _find_order(order_id: str) -> dict | None:
    for order in _STATE.orders:
        if order["order_id"] == order_id:
            return dict(order)
    return None


def _find_refund(refund_id: str) -> dict | None:
    for refund in _STATE.refunds:
        if refund["refund_id"] == refund_id:
            return dict(refund)
    return None


def _create_refund(order_id: str, amount: float, reason: str) -> tuple[int, dict]:
    order = _find_order(order_id)
    if order is None:
        return 404, {"error": "订单不存在", "order_id": order_id}
    if not order.get("refundable"):
        return 409, {"error": "订单不可退", "order_id": order_id, "status": order["status"]}
    if float(amount) > float(order["amount"]):
        return 409, {
            "error": "退款金额超出订单金额",
            "requested": amount,
            "order_amount": order["amount"],
        }
    with _LOCK:
        refund_id = f"RFD-{len(_STATE.refunds) + 1:04d}"
        refund = {
            "refund_id": refund_id,
            "order_id": order_id,
            "amount": amount,
            "reason": reason,
            "status": "created",
        }
        _STATE.refunds.append(refund)
        _save("refunds.json", _STATE.refunds)
    return 201, refund


def _search_products(q: str) -> list[dict]:
    if not q:
        return list(_STATE.products)
    lowered = q.lower()
    return [
        dict(item)
        for item in _STATE.products
        if lowered in item["name"].lower() or lowered in item["faq"].lower()
    ]


class _Handler(BaseHTTPRequestHandler):
    """路由：GET /orders/{id}、GET /orders?user_key=、POST /refunds、
    GET /refunds/{id}、GET /products/search?q=。"""

    def _json(self, status: int, body: dict | list) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # http.server 约定
        path = self.path
        if path.startswith("/orders/"):
            order_id = path.removeprefix("/orders/")
            order = _find_order(order_id)
            if order is None:
                self._json(404, {"error": "订单不存在", "order_id": order_id})
            else:
                self._json(200, order)
            return
        if path.startswith("/orders"):
            _, _, query = path.partition("?")
            user_key = ""
            for pair in query.split("&"):
                if pair.startswith("user_key="):
                    user_key = unquote(pair.removeprefix("user_key="))
            if user_key:
                orders = [dict(o) for o in _STATE.orders if o["user_key"] == user_key]
            else:
                orders = [dict(o) for o in _STATE.orders]
            self._json(200, orders)
            return
        if path.startswith("/refunds/"):
            refund_id = path.removeprefix("/refunds/")
            refund = _find_refund(refund_id)
            if refund is None:
                self._json(404, {"error": "退款单不存在", "refund_id": refund_id})
            else:
                self._json(200, refund)
            return
        if path.startswith("/products/search"):
            _, _, query = path.partition("?")
            q = ""
            for pair in query.split("&"):
                if pair.startswith("q="):
                    q = unquote(pair.removeprefix("q="))
            self._json(200, _search_products(q))
            return
        self._json(404, {"error": "未知端点", "path": path})

    def do_POST(self) -> None:  # http.server 约定
        path = self.path
        if path == "/refunds":
            body = self._read_body()
            order_id = str(body.get("order_id", ""))
            amount = body.get("amount")
            reason = str(body.get("reason", ""))
            try:
                amount = float(amount) if amount is not None else 0.0
            except (TypeError, ValueError):
                self._json(422, {"error": "amount 必须是数字"})
                return
            status, result = _create_refund(order_id, amount, reason)
            self._json(status, result)
            return
        self._json(404, {"error": "未知端点", "path": path})

    def log_message(self, fmt: str, *args: object) -> None:  # 静默默认 stderr 日志
        return


def serve(port: int = _DEFAULT_PORT) -> ThreadingHTTPServer:
    """启动 HTTP 服务并返回 server 实例（测试可 hold 引用防 GC）。"""
    _STATE.reload()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    return server


def main() -> None:
    server = serve()
    print(f"mock-biz 服务已启动: http://127.0.0.1:{_DEFAULT_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭中...")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
