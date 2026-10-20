"""框架缺省常量：零配置兜底与用户扩展约定（非 Pydantic 模型）。"""

DEFAULT_SQLITE_URL: str = "sqlite+aiosqlite:///./lubanagent.db"

# 约定优于配置的用户扩展扫描入口；框架 import 期不导入，运行期由 extension.discover 动态导入
USER_EXTENSION_PACKAGES: tuple[str, ...] = ("app.tools", "app.graphs")

FAKE_MODEL_KEY: str = "fake-chat"

# 零密钥兜底模型定义（D1-9「零外部密钥环境全绿」依据）
# 形状 = ModelDefinition.model_dump(mode="json")，可直接 model_validate 消费（D1-8 种子落库）
SEED_MODEL_DEFINITIONS: list[dict] = [
    {
        "key": FAKE_MODEL_KEY,
        "provider": "fake",
        "model_name": FAKE_MODEL_KEY,
        "factory": "fake",
        "capability": {
            "chat": True,
            "streaming": True,
            "tool_calling": True,
            "structured_output": True,
            "vision": True,
            "reasoning": True,
            "context_window": 128_000,
            "max_output": 4_096,
        },
        "params": {"script": ["你好，我是 LubanAgent。"]},
        "base_url": None,
        "api_key_env": None,
        "enabled": True,
    }
]

SEED_BRAIN_MAPPINGS: dict[str, str] = {
    "fast": FAKE_MODEL_KEY,
    "smart": FAKE_MODEL_KEY,
    "cheap": FAKE_MODEL_KEY,
    "vision": FAKE_MODEL_KEY,
}
