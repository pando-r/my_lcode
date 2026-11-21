"""File-backed teammate mailbox with per-recipient locking."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lcode.team.filelock import acquire
from lcode.team.mailbox.message import Message, MessageType
from lcode.team.persistence import atomic_write_json, read_json


class Box:
    def __init__(self, dir_: str) -> None:
        self._dir = str(dir_)
        Path(self._dir).mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> Path:
        return Path(self._dir) / f"{agent_id}.json"

    def _lock_path(self, agent_id: str) -> str:
        return str(Path(self._dir) / f"{agent_id}.lock")

    def _read_raw(self, agent_id: str) -> list[dict]:
        try:
            data = read_json(self._path(agent_id))
        except FileNotFoundError:
            return []
        return data if isinstance(data, list) else []

    async def write(self, agent_id: str, message: Message) -> None:
        async with acquire(self._lock_path(agent_id)):
            messages = self._read_raw(agent_id)
            if not message.timestamp:
                message.timestamp = datetime.now(UTC).isoformat()
            messages.append(message.to_dict())
            atomic_write_json(self._path(agent_id), messages)

    async def read(self, agent_id: str) -> list[Message]:
        async with acquire(self._lock_path(agent_id)):
            return [Message.from_dict(item) for item in self._read_raw(agent_id)]

    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]:
        async with acquire(self._lock_path(agent_id)):
            indices: list[int] = []
            messages: list[Message] = []
            for index, item in enumerate(self._read_raw(agent_id)):
                message = Message.from_dict(item)
                if not message.read:
                    indices.append(index)
                    messages.append(message)
            return indices, messages

    async def mark_read(self, agent_id: str, indices: list[int]) -> None:
        if not indices:
            return
        async with acquire(self._lock_path(agent_id)):
            messages = self._read_raw(agent_id)
            changed = False
            for index in indices:
                if 0 <= index < len(messages):
                    messages[index]["read"] = True
                    changed = True
            if changed:
                atomic_write_json(self._path(agent_id), messages)


__all__ = ["Box", "Message", "MessageType"]
