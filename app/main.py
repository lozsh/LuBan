"""用户领地装配根：create_app + 注册扩展（客服示例种子）。

这是 scaffold-seed：复制改造后挂自己的扩展。框架不经 import 依赖本模块。
"""

import logging
from pathlib import Path

from fastapi import FastAPI

from lubanagent.app.factory import create_app as _framework_create_app
from lubanagent.config import get_settings
from lubanagent.extension import default_registry
from lubanagent.mcp import McpRegistry
from lubanagent.persistence import DatabaseMcpServerStore, create_database
from lubanagent.skill import SkillRegistry

from .knowledge_sources import (
    ingest_product_faq,
    ingest_project_docs,
    make_product_faq_connector,
    make_project_docs_connector,
)
from .multiagents import build_cs_team

logger = logging.getLogger(__name__)


def create_app(*, ui_dist: Path | None = None) -> FastAPI:
    """装配根：在框架 create_app 基础上挂载客服示例扩展。

    1. 扫描 app.tools / app.graphs（约定扫描入口，框架已做）
    2. 注册 SkillRegistry（app/skills/ 扫描）
    3. 灌入产品 FAQ / 项目文档知识源（store/embedder 复用工厂装配，EMBEDDING_MODEL
       未配则 FakeEmbedding——选择逻辑在 factory，本模块不再自建）
    4. 挂载客服分流团队自定义路由
    """
    settings = get_settings()
    db = create_database(settings)

    # Skill 注册表（settings.skills_root 扫描，缺省 app/skills）
    skills_root = Path(settings.skills_root)
    skill_registry = SkillRegistry(skills_root=skills_root)
    # MCP Server 登记处（mcp_servers 表 enabled 记录；工具发现按 Agent 定义引用时进行）
    mcp_registry = McpRegistry(repository=DatabaseMcpServerStore(db))

    # 启动钩子：scan Skill 包 + 灌入知识源（经 factory lifespan 调用；
    # on_event("startup") 会被 lifespan 覆盖不执行，故挂 on_startup）
    async def _ingest_knowledge() -> None:
        try:
            await skill_registry.scan()
            await ingest_product_faq(store, embedder)
            await ingest_project_docs(store, embedder)
        except Exception as exc:
            # 知识源灌入失败不阻断启动（零配置兜底），但显式告警不静默
            logger.warning("知识源灌入失败, 不阻断启动: %s", exc)

    from .api.teams import router as teams_router

    app = _framework_create_app(
        settings=settings,
        registry=default_registry,
        db=db,
        on_startup=_ingest_knowledge,
        extra_routers=(teams_router,),
        ui_dist=ui_dist,
        skill_registry=skill_registry,
        mcp_registry=mcp_registry,
    )

    # 知识库 store/embedder 复用工厂装配的同一实例（app.state.knowledge_*）：
    # 种子源灌库与工作台托管源共用向量空间与 embedder
    store = app.state.knowledge_store
    embedder = app.state.knowledge_embedder
    make_product_faq_connector(store, embedder)
    make_project_docs_connector(store, embedder)  # 部门项目文档知识源（dept-project 用）

    # SkillRegistry 挂 app.state 供 skills 路由经 request.app.state 取用
    # （api → skill 为分层禁忌，路由不 import skill 模块）
    app.state.skill_registry = skill_registry
    app.state.cs_team = build_cs_team()

    return app


__all__ = ["create_app"]
