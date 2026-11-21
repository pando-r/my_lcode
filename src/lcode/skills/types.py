"""Skill data types shared by parser, loader, and executor."""

# 数据模型定义
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class SkillSource(StrEnum):
    # 用户级（~/.lcode/skills）或项目级（<workspace>/.lcode/skills）。
    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True)
class SkillMeta:
    # SKILL.md frontmatter 的规范化结果。
    name: str
    description: str
    mode: Literal["inline", "fork"] = "inline"
    context: Literal["full", "recent", "none"] = "full"
    model: str | None = None

    def is_fork(self) -> bool:
        return self.mode == "fork"


@dataclass
class ActiveEntry:
    # 已激活 Skill 的快照：name + 激活那一刻的 SOP 正文。
    name: str
    body: str
