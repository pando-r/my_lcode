from __future__ import annotations

import mcp.types as mtypes

from lcode.mcp.tool import adapt_tool


class StubSession:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    async def call_tool(self, name, arguments):
        if self.exc:
            raise self.exc
        return self.result


def _tool(name, description=None, read_only_hint=None, schema=None):
    annotations = (
        mtypes.ToolAnnotations(read_only_hint=read_only_hint)
        if read_only_hint is not None
        else None
    )
    return mtypes.Tool(
        name=name,
        description=description,
        input_schema=schema or {"type": "object"},
        annotations=annotations,
    )


def _result(texts, is_error=False):
    return mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text=t) for t in texts],
        is_error=is_error,
    )


def test_adapt_naming():
    tool = adapt_tool("demo", _tool("echo"), StubSession())
    assert tool.name() == "mcp__demo__echo"
    assert tool.description() == "来自 MCP server demo 的工具 echo"


def test_adapt_description_fallback():
    tool = adapt_tool("demo", _tool("echo", description=""), StubSession())
    assert "echo" in tool.description()


def test_adapt_illegal_name():
    assert adapt_tool("demo", _tool("bad.name"), StubSession()) is None


def test_adapt_read_only():
    assert adapt_tool("d", _tool("t", read_only_hint=True), StubSession()).read_only is True
    assert adapt_tool("d", _tool("t"), StubSession()).read_only is False


async def test_execute_success():
    tool = adapt_tool("d", _tool("t"), StubSession(result=_result(["a", "b"])))
    r = await tool.execute("{}")
    assert not r.is_error
    assert r.content == "a\nb"


async def test_execute_is_error():
    tool = adapt_tool("d", _tool("t"), StubSession(result=_result(["err"], is_error=True)))
    r = await tool.execute("{}")
    assert r.is_error


async def test_execute_exception():
    tool = adapt_tool("d", _tool("t"), StubSession(exc=RuntimeError("boom")))
    r = await tool.execute("{}")
    assert r.is_error
    assert "MCP 工具调用失败" in r.content
