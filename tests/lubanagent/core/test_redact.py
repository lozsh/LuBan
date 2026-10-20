"""共享脱敏组件：字段级规则、展示副本语义与自定义键注册。"""

from lubanagent.core.redact import redact_value, register_secret_keys


def test_redact_value_hides_secret_keys_and_credential_shapes() -> None:
    source = {
        "tool": "refund",
        "order_id": "A-1",
        "api_key": "sk-synthetic-secret",
        "Authorization": "Bearer sk-synthetic-secret",
        "nested": {"session_token": "tok-synthetic", "note": "ok"},
        "callback": "https://local.invalid?api_key=sk-url-secret",
        "plain_url": "http://host.internal/v1",
    }
    copy = redact_value(source)
    assert copy["tool"] == "refund"
    assert copy["order_id"] == "A-1"
    assert copy["api_key"] == "[REDACTED]"
    assert copy["Authorization"] == "[REDACTED]"
    assert "tok-synthetic" not in str(copy["nested"])
    assert copy["nested"]["note"] == "ok"
    assert copy["callback"] == "[REDACTED]"
    # 普通业务地址保留，安全字段仍可排查
    assert copy["plain_url"] == "http://host.internal/v1"
    # 展示副本不改源数据
    assert source["api_key"] == "sk-synthetic-secret"


def test_redact_value_keeps_business_text_for_troubleshooting() -> None:
    payload = {"args": {"order_id": "A-1", "reason": "用户申请退款"}}
    assert redact_value(payload) == payload


def test_register_secret_keys_extends_field_rules() -> None:
    register_secret_keys("internal_staff_no")
    copy = redact_value({"internal-staff-no": "S-007", "other": "v"})
    assert copy["internal-staff-no"] == "[REDACTED]"
    assert copy["other"] == "v"
