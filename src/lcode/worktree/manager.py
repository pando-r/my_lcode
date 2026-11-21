"""Worktree manager: construction, registry, and lifecycle delegation."""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lcode.worktree.git import _git_env, _resolve_head_sha_from_fs, warn
from lcode.worktree.session import (
    WorktreeSession,
    clear_session,
    load_session,
    save_session,
)

if TYPE_CHECKING:
    from lcode.worktree.lifecycle import (
        AutoCleanupReport,
        ExitAction,
        ExitOptions,
        ExitReport,
    )

DEFAULT_SYMLINK_DIRS = ["node_modules", ".venv", "vendor"]
EPHEMERAL_NAME_RE = re.compile(r"^agent-a[0-9a-f]{7}$")


@dataclass
class Worktree:
    name: str
    path: str
    branch: str
    based_on: str
    head_commit: str
    created: datetime
    manual: bool


class Manager:
    def __init__(self, repo_root: str) -> None:
        self.repo_root = str(Path(repo_root).resolve())
        self._validate_repo_root()
        self.worktree_dir = str(Path(self.repo_root) / ".lcode" / "worktrees")
        self.session_file = Path(self.repo_root) / ".lcode" / "worktree_session.json"
        self.symlink_dirs = list(DEFAULT_SYMLINK_DIRS)
        self.lock = asyncio.Lock()
        self.active: dict[str, Worktree] = {}
        self._current_session: WorktreeSession | None = None

        Path(self.worktree_dir).mkdir(parents=True, exist_ok=True)
        self._load_current_session()
        self._scan_existing()

    def _validate_repo_root(self) -> None:
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_root, "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                env=_git_env(),
                check=False,
            )
        except OSError as exc:
            raise ValueError(f"无法执行 git: {exc}") from exc
        if result.returncode != 0:
            raise ValueError(f"不是 Git 仓库: {self.repo_root}")
        toplevel = result.stdout.strip()
        if Path(toplevel).resolve() != Path(self.repo_root):
            raise ValueError(f"{self.repo_root} 不是 Git 仓库根目录（实际根: {toplevel}）")

    def _load_current_session(self) -> None:
        try:
            session = load_session(self.session_file)
        except Exception as exc:  # noqa: BLE001 - N5: corrupt file must not block start
            warn(f"worktree session 损坏，已清空: {exc}")
            self._safe_clear_session()
            return
        if session is None:
            return
        if not Path(session.worktree_path).is_dir():
            warn("session worktree gone, cleared")
            self._safe_clear_session()
            return
        self._current_session = session

    def _safe_clear_session(self) -> None:
        try:
            clear_session(self.session_file)
        except OSError as exc:
            warn(f"清空 worktree session 失败: {exc}")
        self._current_session = None

    def _scan_existing(self) -> None:
        # 快速恢复：只读文件系统，不调用 git。
        directory = Path(self.worktree_dir)
        if not directory.is_dir():
            return
        for path in sorted(directory.iterdir()):
            if not path.is_dir() or not (path / ".git").exists():
                continue
            flat = path.name
            name = flat.replace("+", "/")
            head = _resolve_head_sha_from_fs(str(path))
            if head is None:
                continue
            try:
                created = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                created = datetime.now()
            self.active[name] = Worktree(
                name=name,
                path=str(path),
                branch=f"worktree-{flat}",
                based_on="",
                head_commit=head,
                created=created,
                manual=EPHEMERAL_NAME_RE.match(flat) is None,
            )

    # ── queries ──────────────────────────────────────────────────────────

    def list(self) -> list[Worktree]:
        return sorted(self.active.values(), key=lambda item: item.name)

    def get(self, name: str) -> Worktree | None:
        return self.active.get(name)

    def current_session(self) -> WorktreeSession | None:
        return self._current_session

    # ── lifecycle (delegated to dedicated modules) ───────────────────────

    async def create(self, name: str, base_ref: str = "HEAD", manual: bool = False):
        from lcode.worktree.create import create

        return await create(self, name, base_ref, manual)

    async def enter(self, name: str) -> WorktreeSession:
        from lcode.worktree.lifecycle import enter

        return await enter(self, name)

    async def exit(self, name: str, action: ExitAction, opts: ExitOptions) -> ExitReport:
        from lcode.worktree.lifecycle import exit_worktree

        return await exit_worktree(self, name, action, opts)

    async def remove(self, name: str, opts: ExitOptions) -> None:
        from lcode.worktree.lifecycle import remove

        await remove(self, name, opts)

    async def auto_cleanup(self, name: str) -> AutoCleanupReport:
        from lcode.worktree.lifecycle import auto_cleanup

        return await auto_cleanup(self, name)

    async def sweep_stale(self, cutoff: datetime) -> list[str]:
        from lcode.worktree.sweep import sweep_stale

        return await sweep_stale(self, cutoff)

    # 供生命周期模块写入 session 文件。
    def _set_current_session(self, session: WorktreeSession | None) -> None:
        self._current_session = session
        save_session(self.session_file, session)


__all__ = [
    "DEFAULT_SYMLINK_DIRS",
    "EPHEMERAL_NAME_RE",
    "Manager",
    "Worktree",
]
