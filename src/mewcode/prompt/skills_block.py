"""Prompt blocks for the skill catalog and activated skills."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillCatalogItem:
    # prompt 层只依赖这两个字段，避免反向依赖 skills 包。
    name: str
    description: str


@dataclass(frozen=True)
class ActiveSkillEntry:
    name: str
    body: str


def render_skills_catalog(items: list[SkillCatalogItem]) -> str:
    # F11：只暴露 name + description，完整 SOP 由 LoadSkill 按需加载。
    if not items:
        return ""
    lines = ["## Available Skills", ""]
    lines += [f"- {item.name}: {item.description}" for item in items]
    lines += [
        "",
        "If the user's request matches a skill, call LoadSkill with the skill name to "
        "activate it. The full SOP will be pinned to the environment context.",
    ]
    return "\n".join(lines)


def render_active_skills_block(entries: list[ActiveSkillEntry]) -> str:
    # 激活后的 SOP 拼到 environment context；为空时返回空串。
    if not entries:
        return ""
    lines = ["## Active Skills"]
    for entry in entries:
        lines += ["", f"### Skill: {entry.name}", "", entry.body.rstrip()]
    return "\n".join(lines)


__all__ = [
    "ActiveSkillEntry",
    "SkillCatalogItem",
    "render_active_skills_block",
    "render_skills_catalog",
]
