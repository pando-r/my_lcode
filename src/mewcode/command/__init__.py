"""Slash-command domain logic (no Textual dependency)."""

from __future__ import annotations

from mewcode.command.builtin_prompt import REVIEW_DIRECTIVE
from mewcode.command.builtins import register_builtins
from mewcode.command.command import Command, Handler, Kind
from mewcode.command.dispatch import parse, parse_with_args
from mewcode.command.registry import Registry
from mewcode.command.skills import (
    register_skill_commands,
    register_skill_management,
    remove_skill_commands,
)
from mewcode.command.ui import (
    UI,
    HookSummary,
    NopUI,
    SkillSummary,
    TeamAccessor,
    TeamMemberSummary,
    TeamSummary,
    WorktreeAccessor,
    WorktreeSummary,
)

__all__ = [
    "Command",
    "Handler",
    "HookSummary",
    "Kind",
    "NopUI",
    "REVIEW_DIRECTIVE",
    "Registry",
    "SkillSummary",
    "UI",
    "TeamAccessor",
    "TeamMemberSummary",
    "TeamSummary",
    "WorktreeAccessor",
    "WorktreeSummary",
    "parse",
    "parse_with_args",
    "register_builtins",
    "register_skill_commands",
    "register_skill_management",
    "remove_skill_commands",
]
