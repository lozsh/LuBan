"""预算闸门单元测试：token / cost / 时长三道闸、按调用计价与 no_pricing 判定（H11）。"""

import time

import pytest

from lubanagent.core import Budget, BudgetExceededError, RunTimeoutError
from lubanagent.runtime import BudgetGuard


def test_token_budget_exceeded() -> None:
    guard = BudgetGuard(Budget(max_tokens=10), timeout_seconds=60)
    guard.consume(input_tokens=4, output_tokens=4)
    guard.enforce()
    assert guard.usage.total == 8
    assert guard.usage.model_calls == 1
    guard.consume(input_tokens=2, output_tokens=2)
    with pytest.raises(BudgetExceededError):
        guard.enforce()


def test_cost_budget_with_pricing() -> None:
    pricing = {"input": 1.0, "output": 2.0}  # 元 / 1k tokens
    guard = BudgetGuard(Budget(max_cost=0.01), timeout_seconds=60, pricing=pricing)
    guard.consume(input_tokens=5, output_tokens=2)  # 0.005 + 0.004 = 0.009
    guard.enforce()
    assert guard.cost == pytest.approx(0.009)
    guard.consume(input_tokens=5, output_tokens=0)
    with pytest.raises(BudgetExceededError):
        guard.enforce()
    assert guard.pricing_missing is False


def test_max_cost_without_pricing_is_skipped_and_flagged() -> None:
    guard = BudgetGuard(Budget(max_cost=0.0001), timeout_seconds=60)
    assert guard.pricing_missing is True
    guard.consume(input_tokens=1000, output_tokens=1000)  # 无价格信息不熔断
    guard.enforce()
    assert guard.cost == 0.0


def test_no_budget_never_raises() -> None:
    guard = BudgetGuard(None, timeout_seconds=60)
    guard.consume(input_tokens=10**9, output_tokens=10**9)
    guard.enforce()
    assert guard.pricing_missing is False


def test_time_deadline_uses_min_of_timeout_and_budget_seconds() -> None:
    guard = BudgetGuard(Budget(max_seconds=0.05), timeout_seconds=60)
    guard.check_time()
    time.sleep(0.08)
    with pytest.raises(RunTimeoutError):
        guard.check_time()


# ------------------------------------------------------------ H11：按调用计价 / 本地归因


def test_consume_returns_call_cost_for_local_attribution() -> None:
    """consume 返回本次成本（本地归因数据源）；共享池成本为各次之和。"""
    guard = BudgetGuard(
        Budget(max_cost=10.0), timeout_seconds=60, pricing={"input": 1.0, "output": 2.0}
    )
    first = guard.consume(input_tokens=1000, output_tokens=500)
    assert first == pytest.approx(1000 / 1000 * 1.0 + 500 / 1000 * 2.0)
    second = guard.consume(input_tokens=100, output_tokens=0)
    assert second == pytest.approx(0.1)
    assert guard.cost == pytest.approx(first + second)


def test_per_call_pricing_overrides_constructor_pricing() -> None:
    """父子/成员模型不同价：显式 pricing 按当次模型单价计费，不回落构造期单价。"""
    guard = BudgetGuard(
        Budget(max_cost=10.0),
        timeout_seconds=60,
        pricing={"input": 1.0, "output": 1.0},  # 父模型单价
    )
    call_cost = guard.consume(
        input_tokens=1000,
        output_tokens=1000,
        pricing={"input": 3.0, "output": 5.0},  # 子模型单价
    )
    assert call_cost == pytest.approx(3.0 + 5.0)
    assert guard.cost == pytest.approx(8.0)  # 按子模型实际单价，而非父单价 2.0


def test_explicit_none_pricing_does_not_fall_back() -> None:
    """显式 pricing=None = 本次调用模型无单价：成本记 0，不回落构造期单价错配计费。"""
    guard = BudgetGuard(
        Budget(max_cost=10.0), timeout_seconds=60, pricing={"input": 1.0, "output": 1.0}
    )
    call_cost = guard.consume(input_tokens=1000, output_tokens=1000, pricing=None)
    assert call_cost == 0.0
    assert guard.cost == 0.0
    guard.enforce()  # 无单价消费不触发成本闸（显式告警由调用方负责，不静默承诺硬上限）


def test_cost_enforce_uses_per_call_priced_pool_total() -> None:
    """共享池成本按各调用当次单价累计；enforce 对累计值判定，无单价时成本恒 0 不误熔断。"""
    guard = BudgetGuard(Budget(max_cost=0.005), timeout_seconds=60)
    guard.consume(input_tokens=1000, output_tokens=0, pricing={"input": 0.003, "output": 0.0})
    guard.enforce()
    guard.consume(input_tokens=1000, output_tokens=0, pricing={"input": 0.004, "output": 0.0})
    with pytest.raises(BudgetExceededError):
        guard.enforce()
