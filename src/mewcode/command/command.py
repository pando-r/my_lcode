"""Command metadata types: execution kind, handler signature, registration record."""

# 类型定义
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.command.ui import UI


class Kind(Enum):
    # 纯本地：只打印，不改 App，不进 history。
    LOCAL = "local"
    # 影响界面：改 App 状态，不进 history。
    UI = "ui"
    # 提示词：注入 user 消息 + 触发回合，进 history。
    PROMPT = "prompt"


Handler = Callable[["UI"], Awaitable[None]]


@dataclass(slots=True)
class Command:
    name: str  # 不带 "/" 前缀，全小写，唯一
    description: str  # 一句话，用于 /help 与补全菜单
    kind: Kind
    handler: Handler
    aliases: list[str] = field(default_factory=list)  # 不带 "/" 前缀，全小写，全局唯一
    hidden: bool = False  # /help 与补全菜单不显示，但 dispatcher 仍可命中
    accepts_args: bool = False  # 允许 "/name args"；handler 通过 ui.command_args() 读取
