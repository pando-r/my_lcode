"""LoadSkill tool: activate a skill's SOP into the environment context."""

from __future__ import annotations

import json

from lcode.skills.loader import SkillLoader
from lcode.tool import Result


class LoadSkillTool:
    # read-only：加载 SOP 不写盘、不联网，因此不弹权限提示（N2）。
    read_only: bool = True

    def __init__(self, loader: SkillLoader | None = None, agent=None) -> None:
        self._loader = loader
        self._agent = agent

    def set_loader(self, loader: SkillLoader) -> None:
        self._loader = loader

    def set_agent(self, agent) -> None:
        self._agent = agent

    def name(self) -> str:
        return "LoadSkill"

    def description(self) -> str:
        return "按需激活一个 Skill：把该 Skill 的完整 SOP 钉到环境上下文。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要激活的 Skill 名称"},
            },
            "required": ["name"],
        }

    async def execute(self, args: str) -> Result:
        if self._loader is None or self._agent is None:
            return Result(content="LoadSkill not properly initialized", is_error=True)

        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)

        name = str(data.get("name", "")).strip()
        if not name:
            return Result(content="缺少参数: name", is_error=True)

        skill = self._loader.get(name)
        if skill is None:
            available = ", ".join(n for n, _ in self._loader.get_catalog()) or "(空)"
            return Result(content=f"未知 Skill: {name}。可用: {available}", is_error=True)

        self._agent.activate_skill(skill.name, skill.prompt_body)
        return Result(content=f"Skill '{skill.name}' activated. SOP pinned to environment context.")


__all__ = ["LoadSkillTool"]
