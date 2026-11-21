"""Slash-command domain logic (no Textual dependency)."""

from __future__ import annotations

from lcode.command.builtin_prompt import REVIEW_DIRECTIVE
from lcode.command.builtins import register_builtins
from lcode.command.command import Command, Handler, Kind
from lcode.command.dispatch import parse, parse_with_args
from lcode.command.registry import Registry
from lcode.command.skills import (
    register_skill_commands,
    register_skill_management,
    remove_skill_commands,
)
from lcode.command.ui import (
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
