# 契约 D0-1：`lubanagent/core` 领域模型与错误体系

> 来源：开发计划 D0-1、架构设计 §2/§3.3、技术方案 §5.2/§7/§13.3、AGENTS.md §4。
> 本契约是编码与 strict-validation 的唯一基准。

## 范围

`lubanagent/core/` 八个文件：`definition.py` `run.py` `trace.py` `brain.py` `model.py` `tool.py` `budget.py` `errors.py` + `__init__.py`（re-export 全部公开名）+ `tests/lubanagent/core/`。

## 接口契约

全部用 **Pydantic v2 BaseModel**（枚举用 `str, Enum`），必须 `model_dump(mode="json")` 可 JSON 序列化。字段默认值用 `Field(default_factory=...)` 避免可变默认值。

### brain.py
- `class BrainProfile(str, Enum)`：`FAST="fast"` `SMART="smart"` `CHEAP="cheap"` `VISION="vision"`（技术方案 §7.6，逐字）

### model.py
- `class ModelCapability(BaseModel)`：`chat: bool=True`、`streaming: bool=True`、`tool_calling: bool=False`、`structured_output: bool=False`、`vision: bool=False`、`reasoning: bool=False`、`context_window: int=8192`、`max_output: int=4096`（技术方案 §7.4 能力清单逐字）
- `class ModelDefinition(BaseModel)`：`key: str`（固定标识，切换模型=改映射指向另一个 key）、`provider: str`、`model_name: str`（发给 API 的模型名）、`factory: str`（工厂键，如 `"openai_compat"`/`"fake"`）、`capability: ModelCapability=Field(default_factory=ModelCapability)`、`params: dict[str, Any]=Field(default_factory=dict)`（固定使用参数）、`base_url: str|None=None`、`api_key_env: str|None=None`（密钥所在环境变量名，**不存密钥本体**）、`enabled: bool=True`

### budget.py
- `class Budget(BaseModel)`：`max_tokens: int|None=None`、`max_cost: float|None=None`、`max_seconds: float|None=None`
- `class TokenEstimate(BaseModel)`：`input_tokens: int=0`、`output_tokens: int=0`、`@property total -> int`

### tool.py
- `class RiskLevel(str, Enum)`：`LOW="low"` `MEDIUM="medium"` `HIGH="high"`
- `class ToolContract(BaseModel)`：`timeout_seconds: float=30.0`、`max_retries: int=0`、`idempotent: bool=False`、`approval_required: bool=False`
- `class ToolDescriptor(BaseModel)`：`key: str`、`name: str`、`description: str=""`、`args_schema: dict[str, Any]=Field(default_factory=dict)`（JSON Schema）、`risk: RiskLevel=RiskLevel.LOW`、`contract: ToolContract=Field(default_factory=ToolContract)`、`signature_hash: str=""`（签名摘要，版本兼容治理用，技术方案 §5.2）

### definition.py
- `class AgentStatus(str, Enum)`：`DRAFT="draft"` `PUBLISHED="published"` `DEPRECATED="deprecated"`
- `class IOContract(BaseModel)`：`input_type: Literal["text","json","multimodal"]="text"`、`output_schema: dict[str, Any]|None=None`、`streaming: bool=True`
- `class BrainConfig(BaseModel)`：`profile: BrainProfile=BrainProfile.SMART`、`fallback_profiles: list[BrainProfile]=Field(default_factory=list)`、`allow_run_override: bool=False`（按运行切换需显式允许，技术方案 §7.5）
- `class KnowledgeConfig(BaseModel)`：`sources: list[str]=[]`、`top_k: int=5`、`cite: bool=True`
- `class MemoryConfig(BaseModel)`：`session: bool=True`、`window_size: int=20`、`summary: bool=False`、`long_term: bool=False`
- `class BoundaryConfig(BaseModel)`：`max_steps: int=16`、`timeout_seconds: float=300.0`、`budget: Budget|None=None`、`allow_human_handoff: bool=False`
- `class AgentDefinition(BaseModel)`：`key: str`、`name: str`、`description: str=""`、`role: str=""`、`greeting: str=""`、`instructions: str=""`（System Prompt）、`prompt_variables: dict[str, str]={}`、`brain: BrainConfig`、`tools: list[str]=[]`（Tool 注册键）、`skills: list[str]=[]`、`knowledge: KnowledgeConfig`、`memory: MemoryConfig`、`boundary: BoundaryConfig`、`io: IOContract`、`tool_signatures: dict[str, str]={}`（Tool 键→签名摘要，版本冻结校验用）、`version: int=1`、`status: AgentStatus=AgentStatus.DRAFT`
- `class AgentVersion(BaseModel)`：不可变快照（技术方案 §5.2"发布即冻结"）：`agent_key: str`、`version: int`、`definition: AgentDefinition`、`status: AgentStatus`、`published_at: datetime|None=None`。模型配置 `model_config = ConfigDict(frozen=True)` 表达不可变。

### run.py
- `class RunStatus(str, Enum)`：`PENDING="pending"` `RUNNING="running"` `WAITING_APPROVAL="waiting_approval"` `COMPLETED="completed"` `FAILED="failed"` `CANCELLED="cancelled"`
- `class EndReason(str, Enum)`：`FINAL_ANSWER="final_answer"` `STEP_LIMIT="step_limit"` `BUDGET_EXCEEDED="budget_exceeded"` `TIMEOUT="timeout"` `CANCELLED="cancelled"` `ERROR="error"` `HUMAN_HANDOFF="human_handoff"`
- `class TokenUsage(BaseModel)`：`input_tokens: int=0`、`output_tokens: int=0`、`model_calls: int=0`、`@property total -> int`
- `class Run(BaseModel)`：`id: str`（uuid 字符串）、`agent_key: str`、`agent_version: int`、`session_id: str|None=None`、`parent_run_id: str|None=None`（子 Run 契约，技术方案 §10.6）、`status: RunStatus=RunStatus.PENDING`、`input: dict[str, Any]={}`、`output: str|None=None`、`brain_profile: BrainProfile`、`model_key: str|None=None`（实际解析到的模型，Trace 七问之一）、`end_reason: EndReason|None=None`、`token_usage: TokenUsage`、`cost: float=0.0`、`error: dict[str, Any]|None=None`（`LubanError.to_dict()["error"]`）、`created_at: datetime`、`started_at: datetime|None=None`、`ended_at: datetime|None=None`
- `DepsT = TypeVar("DepsT")`；`class RunContext(BaseModel, Generic[DepsT])`：`run_id: str`、`agent_key: str`、`agent_version: int`、`session_id: str|None=None`、`brain_profile: BrainProfile`、`model_key: str|None=None`、`budget: Budget|None=None`、`usage: TokenUsage`、`deps: DepsT|None=None`、`metadata: dict[str, Any]={}`

### trace.py
- `class TraceEventType(str, Enum)`：`RUN_STARTED` `RUN_COMPLETED` `RUN_FAILED` `RUN_CANCELLED` `STEP_STARTED` `STEP_COMPLETED` `MODEL_CALL_STARTED` `MODEL_TOKEN_DELTA` `MODEL_CALL_COMPLETED` `TOOL_CALL_STARTED` `TOOL_CALL_COMPLETED` `TOOL_CALL_FAILED` `TOOL_APPROVAL_REQUESTED` `TOOL_APPROVAL_RESOLVED` `BUDGET_WARNING` `BUDGET_EXCEEDED` `MODEL_FALLBACK`（值 = 小写名，如 `RUN_STARTED="run_started"`）
- `class TraceEvent(BaseModel)`：`id: str`、`run_id: str`、`type: TraceEventType`、`step_index: int|None=None`、`payload: dict[str, Any]={}`、`created_at: datetime`
- `class Step(BaseModel)`：`index: int`、`kind: Literal["model","tool","graph_node"]`、`name: str`、`started_at: datetime`、`ended_at: datetime|None=None`、`token_usage: TokenUsage`、`error: dict[str, Any]|None=None`
- `class Trace(BaseModel)`：`id: str`、`run_id: str`、`status: RunStatus`、`started_at: datetime`、`ended_at: datetime|None=None`

### errors.py
- `class ErrorCode(str, Enum)`，逐字：`AGENT_NOT_FOUND` `AGENT_VERSION_NOT_FOUND` `AGENT_VERSION_UNRUNNABLE` `MODEL_NOT_FOUND` `BRAIN_NOT_MAPPED` `CAPABILITY_MISMATCH` `NO_FALLBACK_AVAILABLE` `TOOL_NOT_FOUND` `TOOL_PERMISSION_DENIED` `TOOL_APPROVAL_REQUIRED` `TOOL_EXECUTION_FAILED` `RUN_NOT_FOUND` `BUDGET_EXCEEDED` `STEP_LIMIT_EXCEEDED` `RUN_TIMEOUT` `RUN_CANCELLED` `AUTH_FAILED` `RATE_LIMITED` `VALIDATION_ERROR` `CONFIG_ERROR` `INTERNAL_ERROR`（值与名相同，如 `AGENT_NOT_FOUND="AGENT_NOT_FOUND"`）
- `class LubanError(Exception)`：属性 `code: ErrorCode`、`message: str`、`details: dict[str, Any]`、`retryable: bool`；`__init__(self, code: ErrorCode, message: str, *, details: dict|None=None, retryable: bool=False)`；`to_dict() -> {"error": {"code": str(code.value), "message", "details", "retryable"}}`；`__str__` 含 code 与 message
- 语义子类（各固定 code，构造不收 code）：`CapabilityMismatchError(CAPABILITY_MISMATCH)`、`BudgetExceededError(BUDGET_EXCEEDED)`、`StepLimitExceededError(STEP_LIMIT_EXCEEDED)`、`RunTimeoutError(RUN_TIMEOUT)`、`RunCancelledError(RUN_CANCELLED)`、`ToolApprovalRequiredError(TOOL_APPROVAL_REQUIRED)`

## 数据契约

即上方各模型字段；全部模型须 `model_dump(mode="json")` → `json.dumps` 成功；`AgentDefinition`/`Run`/`TraceEvent` 须能 `model_validate` 往返无损（DB JSON 存储前置）。

## 行为契约

1. 纯数据层：无 IO、无网络、无全局单例副作用（代码强制）。
2. 错误体系：`LubanError` 各子类 `to_dict()` 的 code 与类绑定一致（代码强制）。
3. `AgentVersion` frozen：修改字段抛 `ValidationError`（代码强制，对应"发布即冻结"）。

## 边界契约

- **`import lubanagent.core` 不加载 DB/HTTP/LangChain 等重依赖**：仅允许 `pydantic` + 标准库；禁止 import `sqlalchemy`/`fastapi`/`langchain*`/`httpx`（开发计划 D0-1 关键架构）。
- core 不依赖任何兄弟包（架构 §3.2 规则 3：最底层）。
- 不实现任何业务逻辑（架构 §3.3）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/core/` 全绿。
2. 子进程测试：`python -c "import sys, lubanagent.core; assert not [m for m in sys.modules if m.split('.')[0] in {'sqlalchemy','fastapi','langchain','langchain_core','httpx'}]"` 通过。
3. 序列化测试：`AgentDefinition`/`Run`/`TraceEvent`/`ModelDefinition` JSON 往返无损。
4. 枚举值测试：`BrainProfile` 四值逐字；`ErrorCode` 21 个码全列。
5. `LubanError.to_dict()` 结构测试；`AgentVersion` frozen 测试。
6. `ruff check lubanagent/core tests/lubanagent/core` 无错。

## 存疑项与决策

1. spec 未给字段级 schema → 按技术方案 §5.2 组成表 + §13.3 七问反推字段，本契约即字段级决策（2026-07-23，主 agent 依 spec 推导）。
2. `EndReason` spec 未列枚举 → 按开发计划 D1-4 验收（"写结束原因"）与技术方案 §13.3（"为什么结束"）定为七值。
