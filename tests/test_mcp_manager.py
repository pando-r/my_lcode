from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mewcode.mcp.config import Config, ServerConfig
from mewcode.mcp.manager import new_manager
from mewcode.mcp.tool import McpTool

SERVER_SCRIPT = str(Path(__file__).parent / "_mcp_test_server.py")


async def test_connect_stdio_server():
    cfg = Config(
        servers={"test": ServerConfig(type="stdio", command=sys.executable, args=[SERVER_SCRIPT])}
    )
    mgr = await new_manager(cfg, "0.1.0")
    try:
        tools = mgr.tools()
        names = [t.name() for t in tools]
        assert "mcp__test__echo" in names
        assert "mcp__test__add" in names

        echo = next(t for t in tools if t.name() == "mcp__test__echo")
        r = await echo.execute('{"message": "hello"}')
        assert not r.is_error
        assert "hello" in r.content

        add = next(t for t in tools if t.name() == "mcp__test__add")
        r = await add.execute('{"a": 2, "b": 3}')
        assert not r.is_error
        assert "5" in r.content
    finally:
        await mgr.close()


async def test_failure_isolation(monkeypatch):
    async def stub_connect(mgr, name, srv, version):
        if name == "bad":
            raise RuntimeError("boom")
        tool = McpTool(
            full_name=f"mcp__{name}__x",
            remote_name="x",
            description="d",
            parameters={},
            read_only=False,
            caller=None,
        )
        async with mgr._lock:
            mgr._tools.append(tool)

    monkeypatch.setattr("mewcode.mcp.manager._do_connect", stub_connect)
    cfg = Config(
        servers={
            "bad": ServerConfig(type="stdio", command="/no/such"),
            "good": ServerConfig(type="stdio", command="echo"),
        }
    )
    mgr = await new_manager(cfg, "0.1.0")
    try:
        assert [t.name() for t in mgr.tools()] == ["mcp__good__x"]
    finally:
        await mgr.close()


async def test_connect_timeout(monkeypatch, capsys):
    async def stub_block(mgr, name, srv, version):
        await asyncio.Event().wait()

    monkeypatch.setattr("mewcode.mcp.manager._do_connect", stub_block)
    monkeypatch.setattr("mewcode.mcp.manager.connect_timeout", 0.2)
    cfg = Config(servers={"s": ServerConfig(type="stdio", command="echo")})
    mgr = await new_manager(cfg, "0.1.0")
    try:
        assert mgr.tools() == []
    finally:
        await mgr.close()
    assert "timeout" in capsys.readouterr().err


async def test_empty_config():
    mgr = await new_manager(Config(), "0.1.0")
    try:
        assert mgr.tools() == []
    finally:
        await mgr.close()
