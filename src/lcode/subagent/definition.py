"""Agent role definitions and load sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from lcode.permission import Mode as PermissionMode

ModelName = Literal["haiku", "sonnet", "opus", "inherit"]


class Source(IntEnum):
    # 定义来源；数值越大优先级越高（同名覆盖）。
    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3  # 占位：本期插件来源恒为空

    def __str__(self) -> str:
        return {
            Source.BUILTIN: "builtin",
            Source.USER: "user",
            Source.PROJECT: "project",
            Source.PLUGIN: "plugin",
        }.get(self, "unknown")


@dataclass
class Definition:
    # 一个 Agent 角色定义（Markdown frontmatter + 正文系统提示）。
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: ModelName = "inherit"
    max_turns: int = 0  # 0 = 沿用全局 MAX_ITERATIONS
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    dont_ask: bool = False  # permissionMode: dontAsk 的独立开关
    background: bool = False
    isolation: str = ""  # "" | "worktree"
    plan_mode_required: bool = False
    system_prompt: str = ""
    file_path: str = ""
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        return self.name == "__fork__"


__all__ = ["Definition", "ModelName", "PermissionMode", "Source"]
