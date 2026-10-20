# 契约 D1-2：`lubanagent/model` 模型工厂与大脑解析（含 FakeLLM）

> 来源：开发计划 D1-2、技术方案 §7 全节、架构设计 §2/§3.3、AGENTS.md §4（逻辑大脑四槽位、OpenAI 兼容优先）。
> 前置：D0-1（core：ModelDefinition/ModelCapability/BrainProfile/AgentDefinition/TraceEvent/errors）已完成。

## 范围

`lubanagent/model/`：`factory.py`、`openai_compat.py`、`brain_resolver.py`、`capability.py`、`fakes.py`、`__init__.py` + `tests/lubanagent/model/`。

## 接口契约

### factory.py
```python
ChatModelFactoryFn = Callable[[ModelDefinition], BaseChatModel]   # langchain_core

class ModelFactory:
    def __init__(self) -> None
    def register(self, name: str, fn: ChatModelFactoryFn) -> None   # 重复名 → LubanError(CONFIG_ERROR)
    def has(self, name: str) -> bool
    def create(self, definition: ModelDefinition) -> BaseChatModel  # 未知 factory → LubanError(CONFIG_ERROR, details={"factory": ...})

default_model_factory = ModelFactory()   # 模块级：预注册 "openai_compat" 与 "fake"

def create_chat_model(definition: ModelDefinition) -> BaseChatModel  # 便捷函数，走 default_model_factory
```

### openai_compat.py
```python
PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"},
    "qwen":     {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "DASHSCOPE_API_KEY"},
    "glm":      {"base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPUAI_API_KEY"},
    "vllm":     {},        # base_url 由 ModelDefinition 提供
    "ollama":   {"base_url": "http://localhost:11434/v1"},  # 本地无密钥
}

def create_openai_compatible(definition: ModelDefinition) -> BaseChatModel:
    """ChatOpenAI(model=definition.model_name, base_url=definition.base_url, api_key=<从 env 读>, **definition.params)。
    - api_key 来源：definition.api_key_env 指向的环境变量；该变量名为 None（如 ollama）→ 传 "EMPTY"；
      变量名存在但环境变量未设置 → LubanError(CONFIG_ERROR, details={"api_key_env": ...})（代码强制：密钥只来自 env）。
    - definition.base_url 为 None 时回退 PROVIDERS[provider]["base_url"]；仍无 → ChatOpenAI 默认。
    """

def openai_compat_definition(*, key: str, provider: str, model_name: str,
                             capability: ModelCapability | None = None,
                             params: dict[str, Any] | None = None,
                             base_url: str | None = None) -> ModelDefinition:
    """便捷构造：base_url/api_key_env 从 PROVIDERS 预填，可被显式参数覆盖；provider 不在 PROVIDERS → LubanError(VALIDATION_ERROR)。"""
```

### brain_resolver.py
```python
class BrainMappingStore(Protocol):   # 契约决策：依赖 Protocol 而非 persistence 具体仓储（分层 + 可 Fake）
    async def get_brain_mapping(self, env: str, profile: BrainProfile) -> ModelDefinition | None: ...

@dataclass(frozen=True)
class BrainResolution:
    profile: BrainProfile            # 请求的 profile
    definition: ModelDefinition      # 最终解析到的模型
    used_fallback: bool              # 是否走了备用
    resolved_profile: BrainProfile   # 实际命中的 profile（降级时 ≠ profile）
    fallback_events: list[TraceEvent]  # 降级时含一条 MODEL_FALLBACK 事件（payload: from_profile/to_profile/model_key/reason）；Trace 持久化由 D1-4/D1-7 接线

class BrainResolver:
    def __init__(self, store: BrainMappingStore, *, env: str = "dev") -> None
    async def resolve(self, profile: BrainProfile) -> ModelDefinition:
        """brain_mappings(env, profile) → ModelDefinition；无映射/映射指向不存在 → LubanError(BRAIN_NOT_MAPPED)"""
```

### capability.py
```python
@dataclass(frozen=True)
class CapabilityReport:
    ok: bool
    missing: list[str]        # 缺失能力名（ModelCapability 字段名）

def required_capabilities(agent: AgentDefinition) -> list[str]:
    """从 Agent 定义推导所需能力：tools 非空 → "tool_calling"；io.output_schema 非空 → "structured_output"；
    io.input_type == "multimodal" → "vision"。"chat" 恒需。"""

def check_capability(agent: AgentDefinition, model: ModelDefinition) -> CapabilityReport:
    """逐项对照 required vs model.capability（Run 启动前校验，技术方案 §7.4）。"""

async def resolve_brain_for_agent(agent: AgentDefinition, resolver: BrainResolver) -> BrainResolution:
    """受控降级（技术方案 §7.7 显式策略）：
    1. resolve(agent.brain.profile) → check_capability 通过 → BrainResolution(used_fallback=False)
    2. 不满足 → 按 agent.brain.fallback_profiles 顺序逐个 resolve + check；首个满足的命中：
       BrainResolution(used_fallback=True, resolved_profile=<命中>, fallback_events=[MODEL_FALLBACK 事件])
    3. 全部不满足 → raise CapabilityMismatchError(details={"missing": ..., "tried": [...]})
    4. 主 profile resolve 抛 BRAIN_NOT_MAPPED 时同样进入 fallback 链（映射缺失视为不可用）。
    """
```

### fakes.py（全仓库测试基建，D1-4/D2-1/D3-* 依赖）
```python
# script 条目三态：str → 文本回复；dict {"tool_calls": [{"name": str, "args": dict, "id": str}]} → 工具调用；
#                 Exception 实例 → 调用时抛出
class ScriptedFakeChatModel(BaseChatModel):   # langchain_core.language_models.chat_models.BaseChatModel
    script: list[Any]
    capability_echo: dict[str, Any] = {}      # 仅记录用，能力声明在 ModelDefinition 层
    # _generate/_agenerate：按序弹出 script 构造 ChatResult（AIMessage；tool_calls 走 AIMessage(tool_calls=[...])）
    # _stream/_astream：把文本按词切 chunk 流式吐出（SSE 链路测试需要）
    # bind_tools(tools, **kw)：返回 self 的浅拷贝并记录 tools（FakeLLM 支持 Tool Calling 测试）
    # _llm_type = "scripted-fake"
    # script 耗尽后再被调用 → LubanError(INTERNAL_ERROR, details={"reason": "script exhausted"})

class RecordingChatModel(BaseChatModel):
    """包装任意 BaseChatModel，调用结果追加写 JSONL（每行 {"type": "text"|"tool_calls"|"error", ...}），供录制。"""
    wrapped: BaseChatModel
    record_path: str

def replay_script(path: str | Path) -> list[Any]:
    """读 JSONL 还原为 script 列表（录制/重放闭环）。"""

def fake_definition(*, key: str = "fake-chat", script: list[Any] | None = None,
                    capability: ModelCapability | None = None) -> ModelDefinition:
    """factory="fake"、provider="fake" 的 ModelDefinition；script 放 params["script"]；capability 缺省全 True。"""

def create_fake(definition: ModelDefinition) -> ScriptedFakeChatModel:
    """从 definition.params["script"] 构造；注册进 default_model_factory（name="fake"）。"""
```

### __init__.py
re-export 上述公开名。`import lubanagent.model` 时完成 openai_compat/fake 两个工厂的预注册（放 `model/__init__.py` 或 factory.py 尾部，确保 `create_chat_model` 开箱可用）。

## 行为契约

1. 切换模型 = 改库映射：`BrainResolver` 每次 resolve 都过 store（不在解析器层缓存映射），改映射后下一次 resolve 立即走新模型（代码强制；测试：改 store 记录 → resolve 结果变化）。
2. 能力匹配在 Run 启动前：绑 Tool 但大脑不支持 tool_calling → 走 fallback 或抛 CapabilityMismatchError，不留到运行中途（代码强制，技术方案 §7.4）。
3. 降级是显式策略并留痕：fallback_events 非空 ⇔ used_fallback（代码强制）。
4. FakeLLM 确定性：同 script 多次跑结果一致；录制→重放往返消息内容一致（代码强制，技术方案 §13.4）。

## 边界契约

- 模型连接信息存库、固定标识引用；业务层零感知厂商差异（AGENTS.md §4）。
- 密钥本体只来自环境变量；代码与库只存 `api_key_env` 名（代码强制）。
- 单测与 CI 零外部密钥、零真实 API：全部经 FakeLLM/录制重放（开发计划 D1-9）。
- model 包只 import `lubanagent.core` + langchain_core/langchain_openai + pydantic + 标准库；禁止 import persistence 具体类（经 BrainMappingStore Protocol 解耦）、禁止 import api/runtime/app.*。
- OpenAI 兼容协议优先：DeepSeek/Qwen/GLM/vLLM/Ollama 一个 `openai_compat` 工厂全覆盖（AGENTS.md §3）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/model/` 全绿（无网络、无密钥）：
   - factory：注册/重复注册报错/未知工厂报错；`create_chat_model(fake_definition(script=["ok"]))` 返回可 `invoke` 出 "ok" 的模型；
   - openai_compat：monkeypatch 环境变量后构造 ChatOpenAI，断言 base_url/model/api_key 传递正确；api_key_env 指向的变量缺失 → CONFIG_ERROR；
   - brain_resolver：Fake store 下 resolve 命中；无映射 → BRAIN_NOT_MAPPED；改映射 → 新模型（验收原文场景）；
   - capability：tools 非空 + tool_calling=False → missing 含 "tool_calling"；`resolve_brain_for_agent` 主不满足→fallback 命中→used_fallback=True 且 fallback_events 含 MODEL_FALLBACK（type/payload 断言）；全不满足→CapabilityMismatchError；
   - fakes：script 三态（文本/tool_calls/异常）逐一断言；`bind_tools` 后 tool_calls 响应可用；录制（RecordingChatModel 包 ScriptedFakeChatModel）→ `replay_script` → 重放结果一致；astream 产出非空 chunk 序列。
2. `ruff check lubanagent/model` 无错。

## 存疑项与决策

1. 降级写 Trace 的持久化依赖 D1-7 → 决策：D1-2 产出 `fallback_events`（TraceEvent 对象），由 D1-4 Runtime 统一落库；本单元断言事件内容即视为"写 Trace"的契约部分达成（2026-07-23）。
2. `BrainResolver` 依赖 persistence 还是 Protocol → 决策：`BrainMappingStore` Protocol（结构化解耦，`ModelRepository` 天然满足）。
3. ollama 无密钥 → 决策：`api_key_env=None` 时向 ChatOpenAI 传 `"EMPTY"`（OpenAI 客户端要求非空）。
