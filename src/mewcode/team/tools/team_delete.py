"""TeamDelete tool."""

from __future__ import annotations

from typing import Any

from mewcode.team.manager import Manager
from mewcode.team.tools.common import parse_args
from mewcode.tool import Result


class TeamDeleteTool:
    read_only = False
    is_system = True

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "TeamDelete"

    def description(self) -> str:
        return "删除一个 Team 及其成员 worktree/session（有活跃成员时需 force）。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string"},
                "force": {"type": "boolean", "default": False},
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
            await self._manager.delete(name, bool(data.get("force", False)))
        except Exception as exc:  # noqa: BLE001
            return Result(content=str(exc), is_error=True)
        return Result(content=f"Team 已删除: {name}")


def new_team_delete_tool(manager: Manager) -> TeamDeleteTool:
    return TeamDeleteTool(manager)


__all__ = ["TeamDeleteTool", "new_team_delete_tool"]
