"""TeamCreate tool."""

from __future__ import annotations

from typing import Any

from lcode.team.manager import Manager
from lcode.team.tools.common import json_result, parse_args
from lcode.tool import Result


class TeamCreateTool:
    read_only = False
    is_system = True

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "TeamCreate"

    def description(self) -> str:
        return "创建一个 Agent Team，返回团队名、后端类型与配置路径。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string"},
                "description": {"type": "string"},
                "agent_type": {"type": "string"},
            },
            "required": ["team_name"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        name = str(data.get("team_name", "")).strip()
        if not name:
            return Result(content="team_name 不能为空", is_error=True)
        try:
            team = await self._manager.create(
                name,
                str(data.get("description", "")),
                str(data.get("agent_type", "")),
            )
        except Exception as exc:  # noqa: BLE001
            return Result(content=str(exc), is_error=True)
        return json_result(
            {
                "team_name": team.sanitized_name,
                "backend": str(team.backend),
                "config_path": team.config_path,
            }
        )


def new_team_create_tool(manager: Manager) -> TeamCreateTool:
    return TeamCreateTool(manager)


__all__ = ["TeamCreateTool", "new_team_create_tool"]
