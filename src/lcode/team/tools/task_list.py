"""Task list: Team tasks when in a Team context, else background tasks."""

from __future__ import annotations

from typing import Any

from lcode.team.manager import Manager
from lcode.team.tasks import Filter, Status, Store
from lcode.team.tools.common import json_result, parse_args, resolve_team
from lcode.tool import Result


class TeamAwareTaskListTool:
    read_only = True
    is_system = True

    def __init__(self, manager: Manager, fallback) -> None:
        self._manager = manager
        self._fallback = fallback

    def name(self) -> str:
        return "TaskList"

    def description(self) -> str:
        return "Team 上下文：列出共享任务（含 is_ready）；否则列出后台子 Agent 任务。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                }
            },
        }

    async def execute(self, args: str) -> Result:
        team = resolve_team(self._manager)
        if team is None:
            return await self._fallback.execute(args)
        try:
            data = parse_args(args)
            status_raw = data.get("status")
            status = Status(status_raw) if status_raw else None
        except (ValueError, TypeError) as exc:
            return Result(content=str(exc), is_error=True)
        tasks = await Store(team.tasks_path).list_(Filter(status=status))
        return json_result([task.to_dict() | {"is_ready": task.is_ready} for task in tasks])


TaskListTool = TeamAwareTaskListTool

__all__ = ["TaskListTool", "TeamAwareTaskListTool"]
