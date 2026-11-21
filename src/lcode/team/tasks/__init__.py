"""Shared team task list with dependency edges and atomic file persistence."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from lcode.team.filelock import acquire
from lcode.team.persistence import atomic_write_json, read_json


class Status(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0
    is_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": str(self.status),
            "assignee": self.assignee,
            "blocked_by": list(self.blocked_by),
            "blocks": list(self.blocks),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            status=Status(data.get("status", Status.PENDING)),
            assignee=str(data.get("assignee", "")),
            blocked_by=list(data.get("blocked_by", [])),
            blocks=list(data.get("blocks", [])),
            created_at=int(data.get("created_at", 0)),
            updated_at=int(data.get("updated_at", 0)),
        )


@dataclass
class Filter:
    status: Status | None = None


@dataclass
class Patch:
    title: str | None = None
    description: str | None = None
    status: Status | None = None
    assignee: str | None = None
    add_blocks: list[str] | None = None
    add_blocked_by: list[str] | None = None
    remove_blocks: list[str] | None = None
    remove_blocked_by: list[str] | None = None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


class Store:
    def __init__(self, path: str) -> None:
        self._path = str(path)
        self._lock = asyncio.Lock()
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = str(Path(self._path).with_suffix(".lock"))

    def _read(self) -> list[Task]:
        try:
            data = read_json(self._path)
        except FileNotFoundError:
            return []
        raw = data.get("tasks", []) if isinstance(data, dict) else []
        return [Task.from_dict(item) for item in raw]

    def _write(self, tasks: list[Task]) -> None:
        atomic_write_json(self._path, {"tasks": [task.to_dict() for task in tasks]})

    async def _locked_read(self) -> list[Task]:
        async with acquire(self._lock_path):
            return self._read()

    async def create(self, task: Task) -> str:
        async with self._lock:
            async with acquire(self._lock_path):
                tasks = self._read()
                now = int(time.time())
                task.id = task.id or f"task_{secrets.token_hex(3)}"
                task.created_at = task.created_at or now
                task.updated_at = now
                task.blocked_by = _dedupe(task.blocked_by)
                task.blocks = _dedupe(task.blocks)
                tasks.append(task)
                self._write(tasks)
                return task.id

    async def get(self, id_: str) -> Task:
        async with self._lock:
            tasks = await self._locked_read()
        for task in tasks:
            if task.id == id_:
                return task
        raise KeyError(f"未知 task_id: {id_}")

    async def list_(self, filter_: Filter | None = None) -> list[Task]:
        async with self._lock:
            tasks = await self._locked_read()
        status = filter_.status if filter_ is not None else None
        if isinstance(status, str):
            status = Status(status)
        completed = {task.id for task in tasks if task.status is Status.COMPLETED}
        for task in tasks:
            task.is_ready = all(blocker in completed for blocker in task.blocked_by)
        if status is not None:
            tasks = [task for task in tasks if task.status is status]
        return tasks

    async def update(self, id_: str, patch: Patch) -> Task:
        async with self._lock:
            async with acquire(self._lock_path):
                tasks = self._read()
                index = next((i for i, task in enumerate(tasks) if task.id == id_), None)
                if index is None:
                    raise KeyError(f"未知 task_id: {id_}")
                task = tasks[index]

                if patch.title is not None:
                    task.title = patch.title
                if patch.description is not None:
                    task.description = patch.description
                if patch.status is not None:
                    task.status = (
                        Status(patch.status) if isinstance(patch.status, str) else patch.status
                    )
                if patch.assignee is not None:
                    task.assignee = patch.assignee

                for other_id in patch.add_blocks or []:
                    task.blocks = _dedupe(task.blocks + [other_id])
                    other = next((t for t in tasks if t.id == other_id), None)
                    if other is not None:
                        other.blocked_by = _dedupe(other.blocked_by + [task.id])
                for other_id in patch.remove_blocks or []:
                    task.blocks = [value for value in task.blocks if value != other_id]
                    other = next((t for t in tasks if t.id == other_id), None)
                    if other is not None:
                        other.blocked_by = [value for value in other.blocked_by if value != task.id]
                for other_id in patch.add_blocked_by or []:
                    task.blocked_by = _dedupe(task.blocked_by + [other_id])
                    other = next((t for t in tasks if t.id == other_id), None)
                    if other is not None:
                        other.blocks = _dedupe(other.blocks + [task.id])
                for other_id in patch.remove_blocked_by or []:
                    task.blocked_by = [value for value in task.blocked_by if value != other_id]
                    other = next((t for t in tasks if t.id == other_id), None)
                    if other is not None:
                        other.blocks = [value for value in other.blocks if value != task.id]

                task.updated_at = int(time.time())
                self._write(tasks)
                return task


__all__ = ["Filter", "Patch", "Status", "Store", "Task"]
