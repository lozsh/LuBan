"""Agent 定义与版本快照: 定义本体是数据库记录, 发布即冻结。"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .brain import BrainProfile
from .budget import Budget


class AgentStatus(str, Enum):  # noqa: UP042  # 契约钉死枚举用 str, Enum
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class IOContract(BaseModel):
    """Agent 输入输出契约。"""

    input_type: Literal["text", "json", "multimodal"] = "text"
    output_schema: dict[str, Any] | None = None
    streaming: bool = True


class BrainConfig(BaseModel):
    """大脑配置; 按运行切换槽位需显式允许。"""

    profile: BrainProfile = BrainProfile.SMART
    fallback_profiles: list[BrainProfile] = Field(default_factory=list)
    allow_run_override: bool = False


class KnowledgeConfig(BaseModel):
    sources: list[str] = Field(default_factory=list)
    top_k: int = 5
    cite: bool = True
    # 防错答守门（代码强制，不靠 Prompt 声明）：全默认时零行为变化（存量兼容）
    score_threshold: float = 0.0  # cosine 低于此值的 snippet 丢弃；0 = 不过滤
    no_hit_fallback: str | None = None  # 过滤后全 miss 的兜底话术；None = 不短路（旧行为）
    max_age_days: int | None = None  # metadata.ingested_at 超龄 chunk 丢弃；None = 不过滤


class MemoryConfig(BaseModel):
    session: bool = True
    window_size: int = 20
    summary: bool = False
    long_term: bool = False


class BoundaryConfig(BaseModel):
    """运行边界: 步数/超时/预算, 确定性规则由代码执行。"""

    max_steps: int = 16
    timeout_seconds: float = 300.0
    budget: Budget | None = None
    allow_human_handoff: bool = False


class AgentDefinition(BaseModel):
    """Agent 定义本体 (库内记录) ; tool_signatures 用于版本冻结校验。"""

    key: str
    name: str
    description: str = ""
    role: str = ""
    greeting: str = ""
    # 虚拟形象（展示用）：data/avatars/{key}/ 下的落盘文件名（avatar.svg/png/glb/gltf）；
    # None = 无形象（前端回退图标）。只进展示层，运行期逻辑零感知。
    avatar: str | None = None
    instructions: str = ""
    prompt_variables: dict[str, str] = Field(default_factory=dict)
    # Prompt 库引用（双轨）：非空时落库前服务端解析 prompt 内容写入 instructions（快照落地），
    # 运行期只读快照、不回查 prompts 表；None = 纯内嵌（存量路径，零行为变化）
    prompt_key: str | None = None
    prompt_version: int | None = None  # 解析时钉住的 prompt 版本（溯源）
    brain: BrainConfig = Field(default_factory=BrainConfig)
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    boundary: BoundaryConfig = Field(default_factory=BoundaryConfig)
    io: IOContract = Field(default_factory=IOContract)
    tool_signatures: dict[str, str] = Field(default_factory=dict)
    version: int = 1
    status: AgentStatus = AgentStatus.DRAFT


class AgentVersion(BaseModel):
    """发布即冻结的不可变版本快照。"""

    model_config = ConfigDict(frozen=True)

    agent_key: str
    version: int
    definition: AgentDefinition
    status: AgentStatus
    published_at: datetime | None = None
