"""Tool abstraction, registry, and shared helpers."""

# lcode 的工具层基础设施文件（tool package 的 __init__.py），它本身不实现任何具体工具，
# 只提供"工具该怎么写、怎么管、怎么安全跑"的公共约定。
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from lcode.llm import ToolDefinition

if TYPE_CHECKING:
    from lcode.config import AudioConfig, VisionConfig

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
        self._order: list[str] = []  # 注册顺序
        self._tools: dict[str, Tool] = {}  # 工具字典，键为工具名称，值为工具实例

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
        # 工具可以自带超时（视觉请求、转写这类慢工具），未声明时用调用方的默认值。
        own_timeout = getattr(tool, "timeout", None)
        if isinstance(own_timeout, int | float) and own_timeout > 0:
            timeout = float(own_timeout)
        try:
            return await asyncio.wait_for(tool.execute(args), timeout)
        except TimeoutError:
            return Result(content=f"工具 {name} 执行超时（{timeout}s）", is_error=True)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a structured result
            return Result(content=f"工具 {name} 异常: {exc}", is_error=True)


def new_default_registry(
    vision: VisionConfig | None = None,
    audio: AudioConfig | None = None,
) -> Registry:
    # 默认工具注册：构造并注册 7 个内置工具；
    # 配好 vision / audio 段时额外挂上 view_image / transcribe_audio
    # （没配就不挂，模型不会看见用不了的工具）。
    from lcode.tool.bash import BashTool
    from lcode.tool.edit_file import EditFileTool
    from lcode.tool.glob_tool import GlobTool
    from lcode.tool.grep_tool import GrepTool
    from lcode.tool.notify import NotifyTool
    from lcode.tool.read_file import ReadFileTool
    from lcode.tool.send_mail import SendMailTool
    from lcode.tool.write_file import WriteFileTool

    registry = Registry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    # notify 排在最后：它是「把人叫回来」的通道，不参与和文件/命令工具的优先级竞争。
    registry.register(NotifyTool())
    # send_mail 默认挂上，但真正能不能发由 .lcode/mail.yaml 的 send_to 白名单决定。
    # 不在这里按配置条件注册，是因为配置可能中途被改，而工具表只在启动时扫一次。
    registry.register(SendMailTool())
    if vision is not None and vision.ready():
        from lcode.tool.view_image import ViewImageTool

        registry.register(ViewImageTool(vision))
    if audio is not None and audio.ready():
        from lcode.tool.transcribe_audio import TranscribeAudioTool

        registry.register(TranscribeAudioTool(audio))
    return registry
