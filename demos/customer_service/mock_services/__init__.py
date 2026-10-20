"""Mock 业务服务包入口（`python -m demos.customer_service.mock_services` 可起）。"""

from .server import main, serve

__all__ = ["main", "serve"]
