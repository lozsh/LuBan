"""应用装配根：settings → db → registry/discover → observability → runtime → FastAPI。"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, FastAPI, Request

from lubanagent import __version__
from lubanagent.api import (
    AppContext,
    InMemoryRateLimiter,
    api_router,
    register_error_handlers,
)
from lubanagent.api.auth import get_principal
from lubanagent.avatar import AvatarManager
from lubanagent.config import Settings, configure_logging, get_settings
from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import Registry, default_registry, discover_packages
from lubanagent.knowledge import (
    EmbeddingProvider,
    FakeEmbedding,
    KnowledgeSourceManager,
    KnowledgeStore,
    OpenAICompatibleEmbedding,
)
from lubanagent.mcp import McpRegistry
from lubanagent.observability import SafeBackend, db_failure_sink, resolve_backend
from lubanagent.persistence import Database, DbApprovalStore, LubanCheckpointer, create_database
from lubanagent.runtime import AgentRuntime, RunRequest
from lubanagent.skill import SkillRegistry
from lubanagent.tool import ApprovalManager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from lubanagent.memory import Summarizer


# create_app(checkpointer=...) 的哨兵：区分「未传（缺省启用 LubanCheckpointer(db)）」
# 与「显式传 None（关闭 checkpoint，resume 显式 CONFIG_ERROR）」（缺口 #2 契约 4）
_CHECKPOINTER_UNSET = object()


def create_app(
    *,
    settings: Settings | None = None,
    registry: Registry | None = None,
    db: Database | None = None,
    on_startup: Callable[[], Awaitable[None]] | None = None,
    extra_routers: Sequence[APIRouter] = (),
    ui_dist: Path | None = None,
    skill_registry: SkillRegistry | None = None,
    summarizer: "Summarizer | None" = None,
    mcp_registry: McpRegistry | None = None,
    user_key_fn: Callable[[RunRequest], str | None] | None = None,
    checkpointer: "LubanCheckpointer | None" = _CHECKPOINTER_UNSET,
) -> FastAPI:
    """装配 FastAPI 应用；种子落库不在此自动做（属 `luban init`，D1-8）。

    可选能力件注入点（H09）：skill_registry / summarizer / mcp_registry / user_key_fn
    全部透传 AgentRuntime；缺省 None = 对应能力不启用（Agent 定义开启而缺依赖时
    运行期显式 CONFIG_ERROR，不静默看似开启）。

    checkpointer（缺口 #2）：未传或缺省 = LubanCheckpointer(db)（普通 Run 逐步
    checkpoint + resume 默认启用）；显式传 None = 关闭（零 checkpoint 写入，
    resume 显式 CONFIG_ERROR）。
    """
    # 1. 配置与日志
    settings = settings if settings is not None else get_settings()
    configure_logging(settings.log_level, settings.log_format)

    # 2. 数据库（迁移不随启动自动跑，走 `luban migrate`）
    db = db if db is not None else create_database(settings)

    # 3. 扩展注册表 + 约定优于配置扫描用户扩展（包不存在记 warning 继续，空脚手架也能起）
    registry = registry if registry is not None else default_registry
    discover_packages(settings.extension_packages, registry=registry)
    if (
        settings.auth_enabled
        and not settings.api_key
        and registry.get_auth() is None
    ):
        # R6 风险登记：鉴权配置错误装配期 fail fast——这种组合下每个 /v1/* 请求都会
        # 被默认 AuthProvider 拒绝（CONFIG_ERROR），起服务只会造成"表面起来了但接口全挂"，
        # 因此直接拒绝启动并给出修复指引（放在 discover 之后：用户扩展可注册 AuthProvider）。
        raise LubanError(
            ErrorCode.CONFIG_ERROR,
            "AUTH_ENABLED 已开启，但既未配置 API_KEY 也未注册自定义 AuthProvider，"
            "默认鉴权将拒绝所有 /v1 请求，服务无法在该配置下提供 API。"
            "修复任选其一：(1) 设置环境变量 API_KEY（配合 AUTH_ENABLED=true）；"
            "(2) 在 Registry 注册自定义 AuthProvider（registry.register_auth，"
            "用户领地经 extension_packages 扫描或 create_app(registry=...) 注入）；"
            "(3) 关闭鉴权（AUTH_ENABLED=false，仅限开发期零配置场景）。",
            details={"auth_enabled": True, "api_key": None, "custom_auth_provider": None},
        )

    # 4. 观测后端：SafeBackend sink 接 db_failure_sink（外发失败落 export_failures），
    #    并注入重试数据源（drain 时回放待重试留痕：成功删、失败计数、超限放弃）
    observability = resolve_backend(settings, registry)
    if isinstance(observability, SafeBackend):
        observability.set_sink(db_failure_sink(db))  # resolve_backend 不挂 sink，装配层接
        observability.set_retry_db(db)

    # 5. 运行时（可选能力件经注入点透传，H09）；审批记录落库（R11：决定不随进程重启丢失）
    if checkpointer is _CHECKPOINTER_UNSET:
        checkpointer = LubanCheckpointer(db)
    approval_manager = ApprovalManager(store=DbApprovalStore(db))
    runtime = AgentRuntime(
        db=db,
        settings=settings,
        registry=registry,
        observability=observability,
        approval_manager=approval_manager,
        skill_registry=skill_registry,
        summarizer=summarizer,
        user_key_fn=user_key_fn,
        mcp_registry=mcp_registry,
        checkpointer=checkpointer,  # 缺省 LubanCheckpointer(db)（缺口 #2 默认启用）
    )

    # 5.5 知识库：embedder 选择（未配 embedding_model 退回 FakeEmbedding）+
    #     托管知识源 manager（工作台 CRUD/重切经 app.state 取用，api→knowledge 分层禁忌）
    knowledge_embedder: EmbeddingProvider = (
        OpenAICompatibleEmbedding(
            model=settings.embedding_model or "",
            base_url=settings.embedding_base_url,
            api_key_env=settings.embedding_api_key_env,
        )
        if settings.embedding_model
        else FakeEmbedding()
    )
    knowledge_store = KnowledgeStore(db)
    knowledge_manager = KnowledgeSourceManager(
        db=db,
        store=knowledge_store,
        embedder=knowledge_embedder,
        registry=registry,
        files_root=Path(settings.knowledge_files_root),
    )

    # 5.6 Agent 虚拟形象（展示用，SVG/PNG/GLB/GLTF）：文件管理器挂 state，
    #     avatars 路由经 app.state 取用（api → avatar 同 knowledge 分层禁忌）
    avatar_manager = AvatarManager(files_root=Path(settings.avatar_files_root))

    # 6. 应用与上下文
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # MCP Server 登记：读 mcp_servers 表 enabled 记录（失败不阻断启动，同种子风格）
        if mcp_registry is not None:
            try:
                await mcp_registry.load_from_db()
            except Exception:
                logger.warning("MCP Server 登记失败, 不阻断启动", exc_info=True)
        # 托管知识源恢复注册（表里有行则注册 connector；失败不阻断启动，同种子风格）
        try:
            await knowledge_manager.ensure_restored()
        except Exception:
            logger.warning("知识源恢复注册失败, 不阻断启动", exc_info=True)
        # 用户领地启动钩子（scan 扩展/灌知识源等；on_event 被 lifespan 覆盖故经此调用）
        if on_startup is not None:
            await on_startup()
        yield
        # 关闭：观测外发收尾 + MCP 长连释放 + 连接池
        if isinstance(observability, SafeBackend):
            await observability.drain()
        if mcp_registry is not None:
            await mcp_registry.aclose()
        await db.close()

    app = FastAPI(title="LubanAgent", version=__version__, lifespan=lifespan)
    app.state.ctx = AppContext(
        settings=settings,
        db=db,
        registry=registry,
        runtime=runtime,
        approval_manager=approval_manager,
        rate_limiter=InMemoryRateLimiter(settings.rate_limit_per_minute),
    )
    # 知识库装配：用户领地（app/main.py 种子源）与 knowledge 路由共用同一 store/embedder/manager
    app.state.knowledge_store = knowledge_store
    app.state.knowledge_embedder = knowledge_embedder
    app.state.knowledge_manager = knowledge_manager
    app.state.avatar_manager = avatar_manager
    register_error_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:  # 不带 /v1 前缀，无需鉴权
        return {"status": "ok"}

    app.include_router(api_router)
    for router in extra_routers:
        app.include_router(router, dependencies=[Depends(get_principal)])

    # 7. 前端静态托管（生产单进程）：lubanagent/ui/dist 存在时挂载 SPA，
    #    非 /v1 路径的 404 回退 index.html（前端路由由 SPA 接管）
    if ui_dist is None:
        ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
    if ui_dist.is_dir():
        from fastapi.exception_handlers import http_exception_handler
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException

        index_html = ui_dist / "index.html"

        @app.exception_handler(StarletteHTTPException)
        async def _spa_fallback(request: Request, exc: StarletteHTTPException):
            path = request.url.path
            if (
                exc.status_code == 404
                and request.method in ("GET", "HEAD")
                and path != "/v1"
                and not path.startswith("/v1/")
                and not path.startswith(("/assets/", "/favicon", "/.well-known/"))
                and not Path(path).suffix
            ):
                return FileResponse(index_html)
            return await http_exception_handler(request, exc)

        # 路由先注册先匹配：/health、/openapi.json、/docs、/v1/* 均优先于此挂载
        app.mount("/", StaticFiles(directory=ui_dist, html=True), name="ui")

    return app


__all__ = ["create_app"]
