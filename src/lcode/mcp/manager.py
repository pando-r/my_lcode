"""MCP connection manager: connect servers concurrently, cache sessions, close cleanly."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from lcode.mcp.config import Config, ServerConfig
from lcode.mcp.tool import McpTool, adapt_tool

connect_timeout: float = 30.0
close_timeout: float = 5.0


@dataclass
class _Session:
    name: str
    client: Client
    protocol_version: str


class Manager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: list[_Session] = []
        self._tools: list[McpTool] = []
        self._stack = AsyncExitStack()

    def tools(self) -> list[McpTool]:
        return list(self._tools)

    async def close(self) -> None:
        try:
            await asyncio.wait_for(self._stack.aclose(), timeout=close_timeout)
        except TimeoutError:
            print(
                f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak",
                file=sys.stderr,
            )


async def new_manager(cfg: Config, version: str) -> Manager:
    mgr = Manager()
    await mgr._stack.__aenter__()
    for name, srv in cfg.servers.items():
        await _connect_one(mgr, name, srv, version)
    mgr._tools.sort(key=lambda t: t.full_name)
    return mgr


async def _connect_one(mgr: Manager, name: str, srv: ServerConfig, version: str) -> None:
    try:
        async with asyncio.timeout(connect_timeout):
            await _do_connect(mgr, name, srv, version)
    except TimeoutError:
        print(
            f"[mcp] warn: connect server {name} timeout after {connect_timeout}s", file=sys.stderr
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[mcp] warn: connect server {name} failed: {exc}", file=sys.stderr)


async def _do_connect(mgr: Manager, name: str, srv: ServerConfig, version: str) -> None:
    client_info = Implementation(name="lcode", version=version)
    if srv.type == "stdio":
        params = StdioServerParameters(
            command=srv.command,
            args=srv.args,
            env={**os.environ, **srv.env},
        )
        client = await mgr._stack.enter_async_context(
            Client(params, mode="auto", client_info=client_info)
        )
    else:  # http
        http_client = await mgr._stack.enter_async_context(httpx2.AsyncClient(headers=srv.headers))
        # mcp 2.x 的 Client 期望「transport 本身」（一个异步上下文管理器），
        # 不是已进入的 (read_stream, write_stream) 元组；让它自己去 enter。
        client = await mgr._stack.enter_async_context(
            Client(
                streamable_http_client(srv.url, http_client=http_client),
                mode="auto",
                client_info=client_info,
            )
        )

    listed = await client.list_tools()
    adapted = [
        t for t in (adapt_tool(name, tool, client) for tool in listed.tools) if t is not None
    ]

    async with mgr._lock:
        mgr._sessions.append(_Session(name, client, getattr(client, "protocol_version", "") or ""))
        mgr._tools.extend(adapted)
