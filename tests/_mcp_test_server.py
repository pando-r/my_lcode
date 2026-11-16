"""Minimal stdio MCP server used by test_mcp_manager.py (not collected by pytest)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="test-server", version="1.0.0")


@server.tool(name="echo", description="echo a message")
def echo(message: str) -> str:
    return f"echo: {message}"


@server.tool(name="add", description="add two numbers")
def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    server.run(transport="stdio")
