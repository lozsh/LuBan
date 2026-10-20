"""Skill 包领域模型 (SkillMeta/SkillPackage)——底层类型, 供 skill 包与 persistence 仓储共用。"""

from pydantic import BaseModel, Field


class SkillMeta(BaseModel):
    """L1 元数据: 扫描常驻, 注入 system prompt 供模型决策。"""

    name: str
    description: str
    version: str = "0.0.0"


class SkillPackage(BaseModel):
    """L1+L2 定位信息: 正文与资源按需读取, 不常驻内存。"""

    meta: SkillMeta
    path: str  # 包目录绝对路径
    scripts: list[str] = Field(default_factory=list)  # 相对路径
    references: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    content_hash: str = ""  # 全包内容 hash, 版本判定依据
