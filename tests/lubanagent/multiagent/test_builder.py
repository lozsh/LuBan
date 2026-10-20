"""TeamBuilder 校验：无 supervisor / 无成员 / 成员含 supervisor → VALIDATION_ERROR。"""

import pytest

from lubanagent.core import ErrorCode, LubanError
from lubanagent.multiagent import TeamBuilder


def test_build_without_supervisor_validation_error():
    with pytest.raises(LubanError) as exc_info:
        TeamBuilder("t").agent("faq").build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_build_without_members_validation_error():
    with pytest.raises(LubanError) as exc_info:
        TeamBuilder("t").supervisor("triage").build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_build_member_contains_supervisor_validation_error():
    with pytest.raises(LubanError) as exc_info:
        TeamBuilder("t").supervisor("triage").agent("faq").agent("triage").build()
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_build_ok_shape():
    team = (
        TeamBuilder("t", description="demo")
        .supervisor("triage")
        .agent("faq", description="政策咨询")
        .agent("refund")
        .route(max_delegations=3)
        .build()
    )
    assert team.name == "t"
    assert team.description == "demo"
    assert team.supervisor_key == "triage"
    assert team.member_keys == ["faq", "refund"]
    assert team.flow.name == "team:t"
