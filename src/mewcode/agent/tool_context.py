"""Per-tool-call context propagated to tools via contextvars."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.agent import Agent
    from mewcode.conversation import Conversation


@dataclass(frozen=True)
class ToolCallContext:
    # 当前执行工具调用的 Agent / 对话；Agent 工具据此判断父对话与嵌套来源。
    agent: Agent
    conv: Conversation
    is_subagent: bool = False
    is_fork: bool = False


_current: ContextVar[ToolCallContext | None] = ContextVar("mewcode_tool_context", default=None)


def current_tool_context() -> ToolCallContext | None:
    return _current.get()


@contextmanager
def bind_tool_context(ctx: ToolCallContext) -> Iterator[None]:
    token = _current.set(ctx)
    try:
        yield
    finally:
        _current.reset(token)


__all__ = ["ToolCallContext", "bind_tool_context", "current_tool_context"]
