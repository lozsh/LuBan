"""错误体系: to_dict 结构、子类固定 code: 契约「验收标准」第 5 条。"""

from lubanagent.core import (
    BudgetExceededError,
    CapabilityMismatchError,
    ErrorCode,
    LubanError,
    RunCancelledError,
    RunTimeoutError,
    StepLimitExceededError,
    ToolApprovalRequiredError,
)


def test_to_dict_structure() -> None:
    err = LubanError(
        ErrorCode.MODEL_NOT_FOUND,
        "模型不存在",
        details={"key": "x"},
        retryable=True,
    )
    assert err.to_dict() == {
        "error": {
            "code": "MODEL_NOT_FOUND",
            "message": "模型不存在",
            "details": {"key": "x"},
            "retryable": True,
        }
    }


def test_defaults_details_and_retryable() -> None:
    err = LubanError(ErrorCode.INTERNAL_ERROR, "boom")
    assert err.details == {}
    assert err.retryable is False
    body = err.to_dict()["error"]
    assert body["details"] == {}
    assert body["retryable"] is False


def test_str_contains_code_and_message() -> None:
    err = LubanError(ErrorCode.RUN_NOT_FOUND, "找不到 run")
    text = str(err)
    assert "RUN_NOT_FOUND" in text
    assert "找不到 run" in text


def test_subclass_fixed_codes() -> None:
    cases = [
        (CapabilityMismatchError("c"), ErrorCode.CAPABILITY_MISMATCH),
        (BudgetExceededError("b"), ErrorCode.BUDGET_EXCEEDED),
        (StepLimitExceededError("s"), ErrorCode.STEP_LIMIT_EXCEEDED),
        (RunTimeoutError("t"), ErrorCode.RUN_TIMEOUT),
        (RunCancelledError("x"), ErrorCode.RUN_CANCELLED),
        (ToolApprovalRequiredError("a"), ErrorCode.TOOL_APPROVAL_REQUIRED),
    ]
    for err, code in cases:
        assert isinstance(err, LubanError)
        assert err.code is code
        assert err.to_dict()["error"]["code"] == code.value


def test_subclass_accepts_details_not_code() -> None:
    err = BudgetExceededError("超支", details={"cost": 2.0})
    assert err.details == {"cost": 2.0}
    assert err.retryable is False
