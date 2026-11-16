"""Task get: Team task when in a Team context, else background task."""

from __future__ import annotations

from typing import Any

from mewcode.team.manager import Manager
from mewcode.team.tasks import Filter, Store
from mewcode.team.tools.common import json_result, parse_args, resolve_team
from mewcode.tool import Result


class TeamAwareTaskGetTool:
    read_only = True
    is_system = True

    def __init__(self, manager: Manager, fallback) -> None:
        self._manager = manager
        self._fallback = fallback

    def name(self) -> str:
        return "TaskGet"

    def description(self) -> str:
        return "Team 上下文：查询共享任务详情；否则查询后台子 Agent 任务。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        team = resolve_team(self._manager)
        if team is None:
            return await self._fallback.execute(args)
        try:
            data = parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        task_id = str(data.get("task_id", "")).strip()
        store = Store(team.tasks_path)
        try:
            task = await store.get(task_id)
        except KeyError as exc:
            return Result(content=str(exc), is_error=True)
        tasks = await store.list_(Filter(status=None))
        current = next((item for item in tasks if item.id == task_id), task)
        detail = task.to_dict()
        detail["is_ready"] = current.is_ready
        return json_result(detail)


TaskGetTool = TeamAwareTaskGetTool

__all__ = ["TaskGetTool", "TeamAwareTaskGetTool"]
