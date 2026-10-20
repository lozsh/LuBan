"""用户工具领地：扫描子包即触发 @tool 注册。"""

from . import customer_service  # 导入即注册

__all__ = ["customer_service"]
