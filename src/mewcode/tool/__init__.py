"""Tool abstraction, registry, and shared helpers."""

# mewcode 的工具层基础设施文件（tool package 的 __init__.py），它本身不实现任何具体工具，
# 只提供"工具该怎么写、怎么管、怎么安全跑"的公共约定。
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from mewcode.llm import ToolDefinition

DEFAULT_TIMEOUT: float = 30.0


@dataclass
class Result:
    # A tool execution result. Never raises to the caller.
    # 工具结果类，包含工具执行结果的内容和是否为错误标志
    content: str
    is_error: bool = False


# 工具基类
@runtime_checkable  # 运行时检查
class Tool(Protocol):
    # Uniform tool abstraction (F1).

    def name(self) -> str: ...

    def description(self) -> str: ...
    # 参数定义
    def parameters(self) -> dict[str, Any]: ...

    read_only: bool  # True = read-only (concurrent-safe + allowed in Plan Mode)

    # 执行函数
    async def execute(self, args: str) -> Result: ...


def _truncate(s: str, max_lines: int, max_chars: int) -> str:
    # 通用裁剪函数
    # 体量截断：按行/字符上限截断，超出尾部标注 [truncated].
    truncated = False
    if len(s) > max_chars:
        s = s[:max_chars]
        truncated = True
    lines = s.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    out = "\n".join(lines)
    if truncated:
        out += "\n[truncated]"
    return out


class Registry:
    # 注册/查找/导出/执行工具.

    def __init__(self) -> None:
        self._order: list[str] = []
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.name()
        if name in self._tools:
            raise ValueError(f"工具重复注册: {name}")
        self._order.append(name)
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def count(self) -> int:
        # 已注册工具数量（O(1)）。
        return len(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
        ]

    def read_only_definitions(self) -> list[ToolDefinition]:
        # Export only read-only tool definitions (Plan Mode).
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
            if self._tools[name].read_only
        ]

    def is_read_only(self, name: str) -> bool:
        tool = self.get(name)
        return tool is not None and tool.read_only

    async def execute(self, name: str, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        tool = self.get(name)
        if tool is None:
            return Result(content=f"未知工具: {name}", is_error=True)
        try:
            return await asyncio.wait_for(tool.execute(args), timeout)
        except TimeoutError:
            return Result(content=f"工具 {name} 执行超时（{timeout}s）", is_error=True)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a structured result
            return Result(content=f"工具 {name} 异常: {exc}", is_error=True)


def new_default_registry() -> Registry:
    # 默认工具注册：构造并注册 6 个工具.
    from mewcode.tool.bash import BashTool
    from mewcode.tool.edit_file import EditFileTool
    from mewcode.tool.glob_tool import GlobTool
    from mewcode.tool.grep_tool import GrepTool
    from mewcode.tool.read_file import ReadFileTool
    from mewcode.tool.write_file import WriteFileTool

    registry = Registry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    return registry
