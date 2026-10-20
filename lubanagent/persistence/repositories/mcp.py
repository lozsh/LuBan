"""MCP Server 仓储：mcp_servers 表 CRUD, 返回 McpServer 领域对象 (D1-6)。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import McpServer
from lubanagent.persistence.models import McpServerRow
from lubanagent.persistence.session import Database


class DatabaseMcpServerStore:
    """每次 list 开短会话读 mcp_servers 表，满足 mcp.McpServerStore 协议。

    仓储绑定 session，而 McpRegistry.load_from_db 由 lifespan/CLI 独立进程调用，
    不能长期持有单个 session —— 用 Database 每次开新会话适配（装配层注入）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[McpServer]:
        async with self._db.session() as session:
            return await McpServerRepository(session).list()


class McpServerRepository:
    """dict -> McpServer 领域对象承载; DB 列结构不变, 方法签名语义不变。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, server: McpServer) -> None:
        row = await self._session.scalar(
            select(McpServerRow).where(McpServerRow.key == server.key)
        )
        if row is None:
            self._session.add(
                McpServerRow(
                    key=server.key,
                    name=server.name,
                    transport=server.transport,
                    config=server.config,
                    enabled=server.enabled,
                )
            )
        else:
            row.name = server.name
            row.transport = server.transport
            row.config = server.config
            row.enabled = server.enabled
        await self._session.flush()

    async def get(self, key: str) -> McpServer | None:
        row = await self._session.scalar(select(McpServerRow).where(McpServerRow.key == key))
        return self._to_domain(row) if row is not None else None

    async def list(self) -> list[McpServer]:
        rows = (await self._session.scalars(select(McpServerRow).order_by(McpServerRow.id))).all()
        return [self._to_domain(row) for row in rows]

    def _to_domain(self, row: McpServerRow) -> McpServer:
        return McpServer(
            key=row.key,
            name=row.name,
            transport=row.transport,
            config=row.config or {},
            enabled=row.enabled if row.enabled is not None else True,
        )
