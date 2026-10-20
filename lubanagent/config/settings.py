"""环境化配置：env 分层加载（.env -> .env.{env} -> 真实环境变量，后者优先）。"""

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from lubanagent.config.defaults import DEFAULT_SQLITE_URL, USER_EXTENSION_PACKAGES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # 环境
    env: Literal["dev", "staging", "prod"] = Field(default="dev", validation_alias="LUBAN_ENV")
    debug: bool = False  # DEBUG
    # 日志
    log_level: str = "INFO"  # LOG_LEVEL
    log_format: Literal["console", "json"] = "console"  # LOG_FORMAT
    # 数据库（async 驱动 URL）
    database_url: str = DEFAULT_SQLITE_URL  # DATABASE_URL
    # 观测（C 方案）：内置 langfuse/langsmith/none；用户注册的后端名也可经 env 选址（架构 §6.4）
    observability_backend: str = "langfuse"  # OBSERVABILITY_BACKEND
    langfuse_host: str | None = None  # LANGFUSE_HOST
    langfuse_public_key: str | None = None  # LANGFUSE_PUBLIC_KEY
    langfuse_secret_key: str | None = None  # LANGFUSE_SECRET_KEY
    langsmith_api_key: str | None = None  # LANGSMITH_API_KEY
    langsmith_project: str | None = None  # LANGSMITH_PROJECT
    # 接入鉴权（F8）
    auth_enabled: bool = True  # AUTH_ENABLED
    api_key: str | None = None  # API_KEY（框架默认 AuthProvider 用）
    rate_limit_per_minute: int = 600  # RATE_LIMIT_PER_MINUTE
    # 业务接入
    biz_base_url: str | None = None  # BIZ_BASE_URL
    # Embedding（RAG 向量化）：配了 embedding_model 走 OpenAI 兼容协议，
    # 未配则 app 装配退回 FakeEmbedding（零密钥演示，不承诺生产检索质量）
    embedding_model: str | None = None  # EMBEDDING_MODEL
    embedding_base_url: str | None = None  # EMBEDDING_BASE_URL
    embedding_api_key_env: str | None = None  # EMBEDDING_API_KEY_ENV
    # 用户扩展扫描包（EXTENSION_PACKAGES，逗号分隔）
    extension_packages: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(USER_EXTENSION_PACKAGES)
    )
    # Skill 包目录（相对 cwd；上传的 Skill 包落此目录子文件夹，运行期扫描装载）
    skills_root: str = "app/skills"  # SKILLS_ROOT
    # 知识库上传文件落盘根目录（相对 cwd；每个知识源一个子目录，重新切块从盘上重读）
    knowledge_files_root: str = "data/knowledge"  # KNOWLEDGE_FILES_ROOT
    # 长期记忆容量（每 agent+user 条数上限，超出淘汰最旧；防长期运行无限增长）
    long_term_memory_capacity: int = 1000  # LONG_TERM_MEMORY_CAPACITY
    avatar_files_root: str = "data/avatars"  # AVATAR_FILES_ROOT

    @field_validator("extension_packages", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # env/.env 里是逗号分隔字符串，NoDecode 关掉 JSON 解析后在此拆分
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def sync_database_url(self) -> str:
        """Alembic 用同步驱动 URL：只映射已知方言，其他原样返回。"""
        return self.database_url.replace("+aiosqlite", "+pysqlite", 1).replace(
            "+asyncmy", "+pymysql", 1
        )

    @classmethod
    def load(cls) -> "Settings":
        """分层入口：先读 os.environ 的 LUBAN_ENV（缺省 dev），再按 .env + .env.{env} 加载。

        优先级：真实环境变量 > .env.{env} > .env。
        """
        env = os.environ.get("LUBAN_ENV", "dev")
        return cls(_env_file=(".env", f".env.{env}"))


@lru_cache
def get_settings() -> Settings:
    """进程级单例配置入口。"""
    return Settings.load()
