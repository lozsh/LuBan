"""Prompt 领域模型: Prompt 库一等实体, 独立版本化, 可被多个 Agent 引用。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptDefinition(BaseModel):
    """Prompt 定义本体（当前版视图）; content 即 Agent instructions 的来源文本。"""

    key: str
    name: str
    description: str = ""
    content: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    version: int = 1


class PromptVersion(BaseModel):
    """发布即冻结的不可变版本快照（与 AgentVersion 同型）。"""

    model_config = ConfigDict(frozen=True)

    prompt_key: str
    version: int
    content: str
    variables: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None
