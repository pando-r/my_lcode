"""Bridge skills package types to prompt-package types (no reverse dependency)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lcode.prompt.skills_block import ActiveSkillEntry, SkillCatalogItem
from lcode.skills.active import ActiveSkills
from lcode.skills.parser import SkillDef

if TYPE_CHECKING:
    from lcode.agent.runtime import SessionRuntime


def to_prompt_items(skills: list[SkillDef]) -> list[SkillCatalogItem]:
    return [SkillCatalogItem(name=skill.name, description=skill.description) for skill in skills]


def to_prompt_entries(active: ActiveSkills) -> list[ActiveSkillEntry]:
    return [ActiveSkillEntry(name=entry.name, body=entry.body) for entry in active.snapshot()]


class RuntimeSkillAgent:
    # 供 LoadSkill/SkillExecutor 使用的轻量门面：Agent 每轮重建，但 runtime 跨轮存在。
    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    def activate_skill(self, name: str, body: str) -> None:
        self._runtime.active_skills.activate(name, body)

    def clear_active_skills(self) -> None:
        self._runtime.active_skills.clear()

    def list_active(self) -> list[str]:
        return self._runtime.active_skills.names()


__all__ = [
    "RuntimeSkillAgent",
    "to_prompt_entries",
    "to_prompt_items",
]
