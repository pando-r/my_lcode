"""Team TaskCreate tool."""

from __future__ import annotations

from typing import Any

from mewcode.team.manager import Manager
from mewcode.team.tasks import Store, Task
from mewcode.team.tools.common import json_result, parse_args, resolve_team
from mewcode.tool import Result


class TaskCreateTool:
    read_only = False
    is_system = True

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "TaskCreate"

    def description(self) -> str:
        return "在 Team 共享任务列表中创建任务，可指定 assignee 与 blocked_by。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "assignee": {"type": "string"},
                "blocked_by": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        team = resolve_team(self._manager)
        if team is None:
            return Result(content="当前没有活跃 Team", is_error=True)
        title = str(data.get("title", "")).strip()
        if not title:
            return Result(content="title 不能为空", is_error=True)
        blocked_by = data.get("blocked_by") or []
        if not isinstance(blocked_by, list):
            return Result(content="blocked_by 必须是数组", is_error=True)
        store = Store(team.tasks_path)
        task_id = await store.create(
            Task(
                id="",
                title=title,
                description=str(data.get("description", "")),
                assignee=str(data.get("assignee", "")),
                blocked_by=[str(item) for item in blocked_by],
            )
        )
        return json_result({"task_id": task_id})


__all__ = ["TaskCreateTool"]
