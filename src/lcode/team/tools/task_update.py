"""Team TaskUpdate tool."""

from __future__ import annotations

from typing import Any

from lcode.team.manager import Manager
from lcode.team.tasks import Filter, Patch, Status, Store
from lcode.team.tools.common import json_result, parse_args, resolve_team
from lcode.tool import Result


def _status(value: Any) -> Status | None:
    if value is None or value == "":
        return None
    return Status(value)


class TaskUpdateTool:
    read_only = False
    is_system = True

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "TaskUpdate"

    def description(self) -> str:
        return "更新 Team 任务字段与依赖关系（add_blocks / add_blocked_by 等）。"

    def parameters(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "task_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "blocked"],
            },
            "assignee": {"type": "string"},
        }
        for key in (
            "add_blocks",
            "add_blocked_by",
            "remove_blocks",
            "remove_blocked_by",
        ):
            properties[key] = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "properties": properties,
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        team = resolve_team(self._manager)
        if team is None:
            return Result(content="当前没有活跃 Team", is_error=True)
        task_id = str(data.get("task_id", "")).strip()
        if not task_id:
            return Result(content="task_id 不能为空", is_error=True)
        try:
            status = _status(data.get("status"))
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        store = Store(team.tasks_path)
        try:
            task = await store.update(
                task_id,
                Patch(
                    title=data.get("title"),
                    description=data.get("description"),
                    status=status,
                    assignee=data.get("assignee"),
                    add_blocks=_list(data.get("add_blocks")),
                    add_blocked_by=_list(data.get("add_blocked_by")),
                    remove_blocks=_list(data.get("remove_blocks")),
                    remove_blocked_by=_list(data.get("remove_blocked_by")),
                ),
            )
        except KeyError as exc:
            return Result(content=str(exc), is_error=True)
        tasks = await store.list_(Filter())
        current = next((item for item in tasks if item.id == task_id), task)
        detail = task.to_dict()
        detail["is_ready"] = current.is_ready
        return json_result(detail)


def _list(value: Any) -> list[str] | None:
    if value is None:
        return None
    return [str(item) for item in value] if isinstance(value, list) else None


__all__ = ["TaskUpdateTool"]
