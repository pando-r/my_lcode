"""Skill system: SKILL.md parsing, discovery, activation, execution, install."""

from __future__ import annotations

from lcode.skills.active import ActiveSkills
from lcode.skills.adapter import RuntimeSkillAgent, to_prompt_entries, to_prompt_items
from lcode.skills.executor import SYSTEM_TOOL_NAMES, SkillExecutor
from lcode.skills.install import (
    InstallError,
    SkillURL,
    install_from_url,
    parse_skill_url,
)
from lcode.skills.loader import PROJECT_SKILLS_DIR, USER_SKILLS_DIR, SkillLoader
from lcode.skills.parser import (
    SkillDef,
    SkillParseError,
    parse_frontmatter,
    parse_skill_dir,
    parse_skill_file,
    substitute_arguments,
)
from lcode.skills.render import render_body
from lcode.skills.types import ActiveEntry, SkillMeta, SkillSource

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
