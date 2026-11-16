"""Skill system: SKILL.md parsing, discovery, activation, execution, install."""

from __future__ import annotations

from mewcode.skills.active import ActiveSkills
from mewcode.skills.adapter import RuntimeSkillAgent, to_prompt_entries, to_prompt_items
from mewcode.skills.executor import SYSTEM_TOOL_NAMES, SkillExecutor
from mewcode.skills.install import (
    InstallError,
    SkillURL,
    install_from_url,
    parse_skill_url,
)
from mewcode.skills.loader import PROJECT_SKILLS_DIR, USER_SKILLS_DIR, SkillLoader
from mewcode.skills.parser import (
    SkillDef,
    SkillParseError,
    parse_frontmatter,
    parse_skill_dir,
    parse_skill_file,
    substitute_arguments,
)
from mewcode.skills.render import render_body
from mewcode.skills.types import ActiveEntry, SkillMeta, SkillSource

__all__ = [
    "PROJECT_SKILLS_DIR",
    "SYSTEM_TOOL_NAMES",
    "USER_SKILLS_DIR",
    "ActiveEntry",
    "ActiveSkills",
    "InstallError",
    "RuntimeSkillAgent",
    "SkillDef",
    "SkillExecutor",
    "SkillLoader",
    "SkillMeta",
    "SkillParseError",
    "SkillSource",
    "SkillURL",
    "install_from_url",
    "parse_frontmatter",
    "parse_skill_dir",
    "parse_skill_file",
    "parse_skill_url",
    "render_body",
    "substitute_arguments",
    "to_prompt_entries",
    "to_prompt_items",
]
