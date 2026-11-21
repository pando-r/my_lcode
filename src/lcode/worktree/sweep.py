"""Stale ephemeral worktree cleanup."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lcode.worktree.git import _has_unpushed_commits, _run_git, warn
from lcode.worktree.lifecycle import ExitOptions
from lcode.worktree.manager import EPHEMERAL_NAME_RE

if TYPE_CHECKING:
    from lcode.worktree.manager import Manager


def random_agent_name() -> str:
    return "agent-a" + secrets.token_hex(4)[:7]


async def sweep_stale(manager: Manager, cutoff: datetime) -> list[str]:
    removed: list[str] = []
    # 重新扫描文件系统，确保外部创建/删除的 worktree 也被纳入。
    manager._scan_existing()
    current = manager.current_session()

    for path in sorted(Path(manager.worktree_dir).iterdir()):
        if not path.is_dir():
            continue
        flat = path.name
        if EPHEMERAL_NAME_RE.match(flat) is None:
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if mtime > cutoff:
            continue
        if current is not None and Path(current.worktree_path).resolve() == path.resolve():
            continue

        name = flat
        try:
            status = await _run_git(str(path), "status", "--porcelain")
            if status.strip():
                continue
            if await _has_unpushed_commits(str(path)):
                continue
        except Exception:  # noqa: BLE001 - fail closed
            continue

        try:
            await manager.remove(name, ExitOptions(discard_changes=True))
        except Exception as exc:  # noqa: BLE001 - one bad worktree must not abort sweep
            warn(f"sweep {name}: {exc}")
            continue
        removed.append(name)
    return removed


__all__ = ["random_agent_name", "sweep_stale"]
