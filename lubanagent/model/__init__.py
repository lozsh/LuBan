"""model: 模型工厂、OpenAI 兼容接入、大脑解析、能力校验与测试 FakeLLM。"""

from .brain_resolver import BrainMappingStore, BrainResolution, BrainResolver
from .capability import (
    CapabilityReport,
    check_capability,
    required_capabilities,
    resolve_brain_for_agent,
)
from .factory import ChatModelFactoryFn, ModelFactory, create_chat_model, default_model_factory
from .fakes import (
    RecordingChatModel,
    ScriptedFakeChatModel,
    create_fake,
    fake_definition,
    replay_script,
)
from .openai_compat import PROVIDERS, create_openai_compatible, openai_compat_definition

# 预注册内置工厂, create_chat_model 开箱可用
default_model_factory.register("openai_compat", create_openai_compatible)
default_model_factory.register("fake", create_fake)

__all__ = [
    "PROVIDERS",
    "BrainMappingStore",
    "BrainResolution",
    "BrainResolver",
    "CapabilityReport",
    "ChatModelFactoryFn",
    "ModelFactory",
    "RecordingChatModel",
    "ScriptedFakeChatModel",
    "check_capability",
    "create_chat_model",
    "create_fake",
    "create_openai_compatible",
    "default_model_factory",
    "fake_definition",
    "openai_compat_definition",
    "replay_script",
    "required_capabilities",
    "resolve_brain_for_agent",
]
