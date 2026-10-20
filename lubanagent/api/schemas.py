"""API 请求/响应模型（Pydantic v2）：路由只做编排，模型集中在此。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    ModelCapability,
    Run,
    Trace,
    TraceEvent,
)

# ---- 镜像别名：与 core 领域模型同形，路由 response_model 直接用 ----
AgentDefinitionOut = AgentDefinition
RunOut = Run


class RunCreateRequest(BaseModel):
    """POST /v1/agents/{key}/runs 请求体；history 为 OpenAI 风格，v1 不查库（D3-2）。"""

    input: str
    session_id: str | None = None
    # 透传观测标签（排查定位用），引擎不做用户管理
    user_id: str | None = Field(default=None, max_length=128)
    version: int | None = None
    variables: dict[str, str] | None = None
    history: list[dict[str, str]] | None = None  # [{"role": ..., "content": ...}]
    instructions_override: str | None = None  # 试跑临时替换 instructions（不入库，Prompt 调试用）


class ModelDefinitionOut(BaseModel):
    """模型定义对外视图：脱敏——只输出 api_key_env（变量名），不输出 base_url 等连接信息。"""

    key: str
    provider: str
    model_name: str
    factory: str
    capability: ModelCapability = Field(default_factory=ModelCapability)
    params: dict[str, Any] = Field(default_factory=dict)
    api_key_env: str | None = None
    enabled: bool = True


class BrainMappingSetRequest(BaseModel):
    """PUT /v1/brain-mappings 请求体；env 缺省取 settings.env。"""

    profile: BrainProfile
    model_key: str
    env: str | None = None


class BrainMappingOut(BaseModel):
    env: str
    profile: BrainProfile
    model_key: str


class TraceOut(BaseModel):
    trace: Trace
    events: list[TraceEvent]


class ApprovalOut(BaseModel):
    """ApprovalRequest（dataclass）镜像。"""

    id: str
    run_id: str
    tool_key: str
    args: dict[str, Any]
    created_at: datetime
    status: str
    decided_by: str | None = None
    decided_at: datetime | None = None


class DecideRequest(BaseModel):
    approved: bool
    decided_by: str = ""


class DecideOut(BaseModel):
    status: str  # "approved" | "rejected"
    decided_by: str | None = None
    decided_at: datetime | None = None


class SkillOut(BaseModel):
    key: str
    name: str
    version: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class GraphSummaryOut(BaseModel):
    """图列表元素；structure_available=False 表示 builder 非 DSL GraphBuilder，无可视化结构。"""

    name: str
    description: str = ""
    structure_available: bool


class GraphNodeOut(BaseModel):
    name: str
    kind: str  # "step" | "agent" | "human"
    agent_key: str | None = None
    input_from: str | None = None
    output_to: str | None = None
    prompt: str | None = None
    summary: str | None = None  # step: fn docstring 首行（节点中文含义）


class GraphEdgeOut(BaseModel):
    from_node: str
    to_node: str


class GraphCondEdgeOut(BaseModel):
    from_node: str
    then_node: str
    otherwise_node: str


class GraphDetailOut(BaseModel):
    name: str
    description: str = ""
    entry: str | None = None
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    cond_edges: list[GraphCondEdgeOut]


# ---- knowledge：工作台知识库管理 ----


class KnowledgeFileOut(BaseModel):
    filename: str
    size: int


class KnowledgeSourceOut(BaseModel):
    """知识源（managed=工作台创建；False 为代码种子源，参数不可改不可删）。"""

    name: str
    description: str = ""
    chunk_size: int | None = None
    overlap: int | None = None
    files: list[KnowledgeFileOut] = Field(default_factory=list)
    chunk_count: int = 0
    managed: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeRechunkRequest(BaseModel):
    """POST /v1/knowledge/sources/{name}/rechunk 请求体；overlap < chunk_size 由知识层校验。"""

    chunk_size: int = Field(gt=0)
    overlap: int = Field(ge=0)


class KnowledgeChunkOut(BaseModel):
    chunk_index: int
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class KnowledgeQueryRequest(BaseModel):
    """POST /v1/knowledge/query 请求体（检索测试）；source=None 检索全部知识源。"""

    query: str = Field(min_length=1)
    source: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)


class KnowledgeHitOut(BaseModel):
    source: str
    chunk_index: int
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class ErrorOut(BaseModel):
    """统一错误响应：{"error": {code, message, details, retryable}}。"""

    error: ErrorBody

    @classmethod
    def of(
        cls,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> "ErrorOut":
        return cls(
            error=ErrorBody(code=code, message=message, details=details or {}, retryable=retryable)
        )
