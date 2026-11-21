"""MCP (Model Context Protocol) client: discover, adapt, and manage external servers."""

from __future__ import annotations

from lcode.mcp.config import Config, ServerConfig, load_config
from lcode.mcp.manager import Manager, new_manager
from lcode.mcp.tool import McpTool

__all__ = [
    "Config",
    "ServerConfig",
    "load_config",
    "Manager",
    "new_manager",
    "McpTool",
]
