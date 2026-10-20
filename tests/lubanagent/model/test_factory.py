"""模型工厂: 注册分发、重复注册与未知工厂报错、便捷入口开箱可用。"""

import pytest

from lubanagent.core import ErrorCode, LubanError, ModelDefinition
from lubanagent.model import (
    ModelFactory,
    create_chat_model,
    create_fake,
    default_model_factory,
    fake_definition,
)


def test_register_and_create() -> None:
    factory = ModelFactory()
    factory.register("fake", create_fake)
    assert factory.has("fake")
    assert not factory.has("nope")
    model = factory.create(fake_definition(script=["ok"]))
    assert model.invoke("hi").content == "ok"


def test_duplicate_register_raises_config_error() -> None:
    factory = ModelFactory()
    factory.register("fake", create_fake)
    with pytest.raises(LubanError) as exc_info:
        factory.register("fake", create_fake)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_unknown_factory_raises_config_error() -> None:
    definition = ModelDefinition(
        key="m1", provider="x", model_name="x", factory="missing-factory"
    )
    with pytest.raises(LubanError) as exc_info:
        ModelFactory().create(definition)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details == {"factory": "missing-factory"}


def test_default_factory_preregistered() -> None:
    assert default_model_factory.has("openai_compat")
    assert default_model_factory.has("fake")


def test_create_chat_model_convenience() -> None:
    model = create_chat_model(fake_definition(script=["ok"]))
    assert model.invoke("hi").content == "ok"
