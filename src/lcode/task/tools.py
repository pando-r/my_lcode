"""Built-in tools for querying and controlling background sub-agents."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from lcode.task.manager import BackgroundTask, Manager, TaskBusy, TaskNotFound
from lcode.tool import Result


def _parse_args(args: str) -> dict[str, Any]:
    try:
        value = json.loads(args or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数不是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("参数必须是 JSON 对象")
    return value


def _task_summary(task: BackgroundTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "status": str(task.status),
        "tool_count": task.tool_count,
        "last_activity": task.last_activity,
    }


def _task_detail(task: BackgroundTask) -> dict[str, Any]:
    return {
        **_task_summary(task),
        "task": task.task,
        "result": task.result,
        "err": str(task.err) if task.err is not None else "",
        "start_time": task.start_time,
        "end_time": task.end_time,
        "usage": asdict(task.usage),
    }


class TaskListTool:
    is_system = True
    read_only = True

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "TaskList"

    def description(self) -> str:
        return "列出当前已知的后台子 Agent 任务（含已完成，可用 SendMessage 续派）。"

    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        tasks = [_task_summary(task) for task in self._manager.list()]
        return Result(content=json.dumps(tasks, ensure_ascii=False, sort_keys=True))


class TaskGetTool:
    is_system = True
    read_only = True

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "TaskGet"

    def description(self) -> str:
        return "查询指定后台子 Agent 任务的完整状态。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = _parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        task = self._manager.get(str(data.get("task_id", "")))
        if task is None:
            return Result(content=f"未知 task_id: {data.get('task_id', '')}", is_error=True)
        return Result(content=json.dumps(_task_detail(task), ensure_ascii=False, sort_keys=True))


class TaskStopTool:
    is_system = True
    read_only = False

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "TaskStop"

    def description(self) -> str:
        return "取消一个正在运行的后台子 Agent 任务。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = _parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        task_id = str(data.get("task_id", ""))
        if not await self._manager.stop(task_id):
            return Result(content=f"未知 task_id: {task_id}", is_error=True)
        return Result(
            content=json.dumps(
                {"task_id": task_id, "status": "cancellation_requested"},
                ensure_ascii=False,
            )
        )


class SendMessageTool:
    is_system = True
    read_only = False

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def name(self) -> str:
        return "SendMessage"

    def description(self) -> str:
        return "给一个已完成且仍存活的后台子 Agent 续派新任务。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["name", "message"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = _parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        name = str(data.get("name", ""))
        message = str(data.get("message", ""))
        if not name or not message:
            return Result(content="name 与 message 均不能为空", is_error=True)
        try:
            task_id = await self._manager.send_message(name, message)
        except TaskNotFound as exc:
            return Result(content=str(exc), is_error=True)
        except TaskBusy as exc:
            return Result(content=str(exc), is_error=True)
        return Result(
            content=json.dumps({"task_id": task_id, "status": "resumed"}, ensure_ascii=False)
        )


__all__ = [
    "SendMessageTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskStopTool",
]
