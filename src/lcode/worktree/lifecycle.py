"""Enter / exit / remove / auto-cleanup for worktrees."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from lcode.worktree.git import _has_worktree_changes, _run_git, warn
from lcode.worktree.session import WorktreeSession

if TYPE_CHECKING:
    from lcode.worktree.manager import Manager, Worktree


class ExitAction(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"


@dataclass
class ExitOptions:
    discard_changes: bool = False


@dataclass
class ExitReport:
    removed: bool
    path: str
    branch: str


@dataclass
class AutoCleanupReport:
    kept: bool
    path: str = ""
    branch: str = ""


class WorktreeHasChangesError(Exception):
    pass


async def enter(manager: Manager, name: str) -> WorktreeSession:
    async with manager.lock:
        worktree = manager.active.get(name)
        if worktree is None:
            raise ValueError(f"未知 Worktree: {name}")

        original_cwd = str(Path.cwd())
        try:
            original_branch = await _run_git(manager.repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        except Exception:  # noqa: BLE001 - metadata is best-effort
            original_branch = ""
        try:
            original_head = await _run_git(manager.repo_root, "rev-parse", "HEAD")
        except Exception:  # noqa: BLE001
            original_head = ""

        session = WorktreeSession(
            original_cwd=original_cwd,
            worktree_path=worktree.path,
            worktree_name=name,
            original_branch=original_branch,
            original_head_commit=original_head,
            session_id=secrets.token_hex(8),
        )
        manager._set_current_session(session)
        return session


async def exit_worktree(
    manager: Manager, name: str, action: ExitAction, opts: ExitOptions
) -> ExitReport:
    async with manager.lock:
        session = manager.current_session()
        if session is None or session.worktree_name != name:
            raise ValueError("只能退出当前 Worktree 会话")
        worktree = manager.active.get(name)
        if worktree is None:
            raise ValueError(f"未知 Worktree: {name}")

        if action is ExitAction.REMOVE and not opts.discard_changes:
            if await _has_worktree_changes(worktree.path, worktree.head_commit):
                raise WorktreeHasChangesError(
                    "Worktree 有未提交修改或本地新增 commit，拒绝删除；请加 --discard 显式丢弃"
                )

        # 唯一允许的 os.chdir：兜底清除会话期间可能残留的进程 cwd。
        with contextlib.suppress(OSError):
            os.chdir(session.original_cwd)
        manager._set_current_session(None)

        if action is ExitAction.REMOVE:
            await _remove_worktree(manager, worktree)
        return ExitReport(
            removed=action is ExitAction.REMOVE,
            path=worktree.path,
            branch=worktree.branch,
        )


async def remove(manager: Manager, name: str, opts: ExitOptions) -> None:
    async with manager.lock:
        worktree = manager.active.get(name)
        if worktree is None:
            raise ValueError(f"未知 Worktree: {name}")
        if not opts.discard_changes:
            if await _has_worktree_changes(worktree.path, worktree.head_commit):
                raise WorktreeHasChangesError(
                    "Worktree 有未提交修改或本地新增 commit，拒绝删除；请加 --discard 显式丢弃"
                )
        session = manager.current_session()
        if session is not None and session.worktree_name == name:
            with contextlib.suppress(OSError):
                os.chdir(session.original_cwd)
            manager._set_current_session(None)
        await _remove_worktree(manager, worktree)


async def auto_cleanup(manager: Manager, name: str) -> AutoCleanupReport:
    worktree = manager.active.get(name)
    if worktree is None:
        return AutoCleanupReport(kept=False)
    if worktree.manual:
        return AutoCleanupReport(kept=True, path=worktree.path, branch=worktree.branch)
    if await _has_worktree_changes(worktree.path, worktree.head_commit):
        return AutoCleanupReport(kept=True, path=worktree.path, branch=worktree.branch)
    await remove(manager, name, ExitOptions(discard_changes=True))
    return AutoCleanupReport(kept=False)


async def _remove_worktree(manager: Manager, worktree: Worktree) -> None:
    try:
        await _run_git(manager.repo_root, "worktree", "remove", "--force", worktree.path)
    except Exception as exc:  # noqa: BLE001 - directory may already be gone
        warn(f"git worktree remove: {exc}")
    await asyncio.sleep(0.1)
    try:
        await _run_git(manager.repo_root, "branch", "-D", worktree.branch)
    except Exception as exc:  # noqa: BLE001 - orphan branch is preferable to a stuck UI
        warn(f"git branch -D {worktree.branch}: {exc}")
    manager.active.pop(worktree.name, None)


__all__ = [
    "AutoCleanupReport",
    "ExitAction",
    "ExitOptions",
    "ExitReport",
    "WorktreeHasChangesError",
    "auto_cleanup",
    "enter",
    "exit_worktree",
    "remove",
]
