"""MCP Server 领域模型 (mcp_servers 表记录)——供 mcp 登记处与 persistence 仓储共用。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class McpServer(BaseModel):
    """一个外部 MCP Server 的登记记录 (D1-6 领域化)。

    连接细节 (command/args/env/url/default_risk/timeout_seconds...) 收在 config 子字段,
    经 mcp 登记处 `_config_from_record` 展开为 McpServerConfig。
    """

    key: str
    name: str = ""
    transport: Literal["stdio", "http"] = "stdio"
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
