"""Command registry with startup-time conflict detection and prefix matching."""

from __future__ import annotations

from mewcode.command.command import Command


class Registry:
    # 名字与别名统一映射到同一个 Command；visible 列表按 name 字典序维护。
    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []

    def register(self, cmd: Command) -> None:
        if not cmd.name or cmd.name != cmd.name.lower():
            raise ValueError(f"命令名必须非空且全小写: {cmd.name!r}")
        for alias in cmd.aliases:
            if not alias or alias != alias.lower():
                raise ValueError(f"命令别名必须非空且全小写: {alias!r}")

        for key in (cmd.name, *cmd.aliases):
            if key in self._by_name:
                raise RuntimeError(f"命令冲突: {key}")

        for key in (cmd.name, *cmd.aliases):
            self._by_name[key] = cmd
        if not cmd.hidden:
            self._visible.append(cmd)
            self._visible.sort(key=lambda c: c.name)

    def lookup(self, name: str) -> Command | None:
        return self._by_name.get(name.lower())

    def remove(self, name: str) -> bool:
        # 移除一个命令及其全部别名；skill 注册时可覆盖同名内置命令（F14）。
        cmd = self._by_name.get(name.lower())
        if cmd is None:
            return False
        for key in [key for key, value in self._by_name.items() if value is cmd]:
            del self._by_name[key]
        self._visible = [item for item in self._visible if item is not cmd]
        return True

    def visible(self) -> list[Command]:
        # 返回副本，避免调用方改动内部列表。
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        # 仅按主名前缀匹配，不匹配别名与描述；"/" 或空串返回全部可见命令。
        p = prefix.strip().lstrip("/").lower()
        if not p:
            return list(self._visible)
        return [cmd for cmd in self._visible if cmd.name.startswith(p)]
