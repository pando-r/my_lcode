"""Session-scoped list of activated skills."""

from __future__ import annotations

import threading

from mewcode.skills.types import ActiveEntry


class ActiveSkills:
    # 保持激活顺序；同名再次激活时原位覆盖，避免 env 中重复出现。
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ActiveEntry] = []
        self._index: dict[str, int] = {}

    def activate(self, name: str, body: str) -> None:
        with self._lock:
            index = self._index.get(name)
            if index is None:
                self._index[name] = len(self._entries)
                self._entries.append(ActiveEntry(name=name, body=body))
            else:
                self._entries[index] = ActiveEntry(name=name, body=body)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._index.clear()

    def snapshot(self) -> list[ActiveEntry]:
        # 拷贝出当前列表，供 environment 装配使用。
        with self._lock:
            return list(self._entries)

    def names(self) -> list[str]:
        with self._lock:
            return [entry.name for entry in self._entries]


__all__ = ["ActiveSkills"]
