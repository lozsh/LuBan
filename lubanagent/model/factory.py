"""模型工厂: ModelDefinition → BaseChatModel, 支持注册自定义工厂。"""

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from lubanagent.core import ErrorCode, LubanError, ModelDefinition

ChatModelFactoryFn = Callable[[ModelDefinition], BaseChatModel]


class ModelFactory:
    """按 definition.factory 名分发到具体构造函数。"""

    def __init__(self) -> None:
        self._factories: dict[str, ChatModelFactoryFn] = {}

    def register(self, name: str, fn: ChatModelFactoryFn) -> None:
        if name in self._factories:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"模型工厂 {name} 已注册",
                details={"factory": name},
            )
        self._factories[name] = fn

    def has(self, name: str) -> bool:
        return name in self._factories

    def create(self, definition: ModelDefinition) -> BaseChatModel:
        fn = self._factories.get(definition.factory)
        if fn is None:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"未知模型工厂: {definition.factory}",
                details={"factory": definition.factory},
            )
        return fn(definition)


default_model_factory = ModelFactory()


def create_chat_model(definition: ModelDefinition) -> BaseChatModel:
    """便捷入口, 走 default_model_factory。"""
    return default_model_factory.create(definition)
