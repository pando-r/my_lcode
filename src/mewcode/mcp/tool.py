"""Adapt remote MCP tools to mewcode's Tool protocol."""

# 把 远端 MCP server 暴露的工具，包装成本地 Tool 协议要求的对象，
# 好让 Registry 无差别地注册、导出、执行。
from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Any, Protocol

import mcp.types as mtypes

from mewcode.tool import Result

_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_non_text_warn_once: set[str] = set()


class CallerSession(Protocol):
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> mtypes.CallToolResult: ...


class McpTool:
    # A remote MCP tool wrapped as a mewcode Tool.

    def __init__(
        self,
        full_name: str,
        remote_name: str,
        description: str,
        parameters: dict[str, Any],
        read_only: bool,
        caller: CallerSession,
    ) -> None:
        self.full_name = full_name
        self.remote_name = remote_name
        self._description = description
        self._parameters = parameters
        self.read_only = read_only
        self.caller = caller

    def name(self) -> str:
        return self.full_name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, args: str) -> Result:
        # JSON解析
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError:
            return Result(content="MCP 工具参数解析失败", is_error=True)
        # 参数归一化
        arg_map = data if isinstance(data, dict) and data else None
        try:
            result = await asyncio.wait_for(
                self.caller.call_tool(self.remote_name, arg_map), timeout=30
            )
        except TimeoutError:
            return Result(content="MCP 工具调用超时 (30s)", is_error=True)
        except Exception as exc:  # noqa: BLE001
            return Result(content=f"MCP 工具调用失败: {exc}", is_error=True)

        texts: list[str] = []
        for block in result.content:
            if isinstance(block, mtypes.TextContent):
                texts.append(block.text)
            else:
                if self.full_name not in _non_text_warn_once:
                    _non_text_warn_once.add(self.full_name)
                    print(
                        f"[mcp] warn: tool {self.full_name} dropped non-text blocks",
                        file=sys.stderr,
                    )
        return Result(content="\n".join(texts), is_error=bool(result.is_error))


def adapt_tool(server_name: str, t: mtypes.Tool, client: CallerSession) -> McpTool | None:
    full_name = f"mcp__{server_name}__{t.name}"
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None
    description = t.description or f"来自 MCP server {server_name} 的工具 {t.name}"
    parameters = dict(t.input_schema) if t.input_schema else {"type": "object"}
    read_only = bool(t.annotations and t.annotations.read_only_hint)
    return McpTool(
        full_name=full_name,
        remote_name=t.name,
        description=description,
        parameters=parameters,
        read_only=read_only,
        caller=client,
    )
