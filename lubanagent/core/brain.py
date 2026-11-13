"""可扩展逻辑大脑标识；四个常量仅为初始预设，目录与映射由数据库管理。"""

import re
from enum import Enum
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

BRAIN_PROFILE_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class BrainProfile(str, Enum):  # noqa: UP042  # 保留既有常量、.value 与预设迭代的兼容接口
    """支持自定义标识；枚举迭代仅列初始预设，不代表当前数据库目录。"""

    FAST = "fast"
    SMART = "smart"
    CHEAP = "cheap"
    VISION = "vision"

    @classmethod
    def _missing_(cls, value: object) -> "BrainProfile | None":
        if not isinstance(value, str) or re.fullmatch(BRAIN_PROFILE_PATTERN, value) is None:
            return None
        member = str.__new__(cls, value)
        member._name_ = value.upper()
        member._value_ = value
        return member

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        # OpenAPI 发布受约束的 string，而不是写死四项的 enum。
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(strict=True, pattern=BRAIN_PROFILE_PATTERN),
            ref="BrainProfile",
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: value.value,
                return_schema=core_schema.str_schema(pattern=BRAIN_PROFILE_PATTERN),
            ),
        )
