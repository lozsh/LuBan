"""预算闸门：token / cost / 时长三道闸，撞线即抛，代码强制。

父子预算共享（§10.6 / H11）：多个 Run 共用一个 guard 实例时，consume 把每次
模型调用按**当次实际单价**累计进同一池（消费原子累计：consume/enforce 之间无
await，事件循环内串行）；每个 Run 另行用返回值做**本地归因**——Run 行/费用账本
只落本 Run 自己的消费，不落共享池累计值（父自身与后代聚合走报表口径，不重复相加）。
"""

import time
from typing import Any

from lubanagent.core import Budget, BudgetExceededError, RunTimeoutError, TokenUsage

_UNSET: Any = object()  # pricing 哨兵：缺省回落构造期单价；显式传 None = 本次调用无单价


class BudgetGuard:
    """运行预算三道闸门：token / cost / 时长（撞线即抛，代码强制）。"""

    def __init__(
        self,
        budget: Budget | None,
        *,
        timeout_seconds: float,
        pricing: dict[str, float] | None = None,
        initial_input_tokens: int = 0,
        initial_output_tokens: int = 0,
        initial_cost: float = 0.0,
    ) -> None:
        self._budget = budget
        limit = timeout_seconds
        if budget is not None and budget.max_seconds is not None:
            limit = min(limit, budget.max_seconds)
        self._deadline = time.monotonic() + limit
        self._limit_seconds = limit
        self._pricing = pricing
        self._usage = TokenUsage(
            input_tokens=initial_input_tokens, output_tokens=initial_output_tokens
        )
        # 预算恢复（缺口 #2）：resume 以账本已消费量初始化，崩溃续跑不重复放水
        self._cost = initial_cost

    def check_time(self) -> None:
        """过 deadline -> RunTimeoutError。"""
        if time.monotonic() > self._deadline:
            raise RunTimeoutError("运行超时", details={"timeout_seconds": self._limit_seconds})

    def consume(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        pricing: dict[str, float] | None = _UNSET,
    ) -> float:
        """累计一次模型调用的用量与成本（按当次单价计费），返回本次成本；不判定限额。

        pricing 缺省回落构造期单价；显式传 None 表示本次调用模型无单价（成本记 0，
        不回落到共享池里其他模型的单价——父子/成员模型价格不同，错配即错账）。
        先累计后判定（enforce 抛错时消费已入池，失败路径消费不消失）。
        """
        if pricing is _UNSET:
            pricing = self._pricing
        self._usage.input_tokens += input_tokens
        self._usage.output_tokens += output_tokens
        self._usage.model_calls += 1
        call_cost = 0.0
        if pricing is not None:
            call_cost = (
                input_tokens / 1000.0 * pricing["input"]
                + output_tokens / 1000.0 * pricing["output"]
            )
            self._cost += call_cost
        return call_cost

    def enforce(self) -> None:
        """限额判定：token / cost 撞线 -> BudgetExceededError（本地归因完成后再调用）。"""
        if self._budget is None:
            return
        if self._budget.max_tokens is not None and self._usage.total > self._budget.max_tokens:
            raise BudgetExceededError(
                "token 预算超限",
                details={"max_tokens": self._budget.max_tokens, "used": self._usage.total},
            )
        # 成本只在有单价时累计（无单价恒 0，不会误熔断）；共享池各调用按各自单价入账
        if self._budget.max_cost is not None and self._cost > self._budget.max_cost:
            raise BudgetExceededError(
                "成本预算超限",
                details={"max_cost": self._budget.max_cost, "cost": self._cost},
            )

    @property
    def usage(self) -> TokenUsage:
        """共享池累计用量（含所有后代消费）；单 Run 本地值由调用方自行归因。"""
        return self._usage

    @property
    def cost(self) -> float:
        """共享池累计成本（含所有后代消费，按各自实际单价计费）。"""
        return self._cost

    @property
    def max_cost(self) -> float | None:
        return self._budget.max_cost if self._budget is not None else None

    @property
    def pricing_missing(self) -> bool:
        """有 max_cost 但无 pricing -> True（调用方写 BUDGET_WARNING）。"""
        return (
            self._budget is not None
            and self._budget.max_cost is not None
            and self._pricing is None
        )
