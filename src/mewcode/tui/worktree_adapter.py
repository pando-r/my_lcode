"""Adapt worktree.Manager to the command layer's WorktreeAccessor protocol."""

from __future__ import annotations

from collections.abc import Callable

from mewcode.command.ui import WorktreeSummary
from mewcode.worktree import ExitAction, ExitOptions, Manager

ACTION_REMOVE = "remove"


class WorktreeAdapter:
    def __init__(
        self,
        manager: Manager,
        set_active_cwd: Callable[[str], None],
    ) -> None:
        self._manager = manager
        self._set_active_cwd = set_active_cwd

    async def create(self, name: str) -> tuple[str, str]:
        worktree = await self._manager.create(name, "HEAD", manual=True)
        return worktree.path, worktree.branch

    def list(self) -> list[WorktreeSummary]:
        session = self._manager.current_session()
        active_name = session.worktree_name if session is not None else ""
        return [
            WorktreeSummary(
                name=worktree.name,
                path=worktree.path,
                branch=worktree.branch,
                active=worktree.name == active_name,
                manual=worktree.manual,
            )
            for worktree in self._manager.list()
        ]

    async def enter(self, name: str) -> None:
        session = await self._manager.enter(name)
        self._set_active_cwd(session.worktree_path)

    async def exit(self, action: str, discard: bool) -> bool:
        session = self._manager.current_session()
        if session is None:
            raise ValueError("当前没有活跃的 Worktree 会话")
        exit_action = ExitAction.REMOVE if action == ACTION_REMOVE else ExitAction.KEEP
        report = await self._manager.exit(
            session.worktree_name,
            exit_action,
            ExitOptions(discard_changes=discard),
        )
        self._set_active_cwd("")
        return report.removed

    async def remove(self, name: str, discard: bool) -> None:
        await self._manager.remove(name, ExitOptions(discard_changes=discard))
        session = self._manager.current_session()
        if session is None or session.worktree_name == name:
            self._set_active_cwd("")


__all__ = ["WorktreeAdapter"]
