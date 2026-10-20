"""预算与 token 估算: Run 级资源上限 (子 Run 继承父 Run 预算) 。"""

from pydantic import BaseModel


class Budget(BaseModel):
    """运行预算上限, 任一为 None 表示该项不限。"""

    max_tokens: int | None = None
    max_cost: float | None = None
    max_seconds: float | None = None


class TokenEstimate(BaseModel):
    """调用前 token 估算。"""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens
