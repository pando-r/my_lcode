"""Create worktrees (with fast recovery) and best-effort environment setup."""

from __future__ import annotations

import fnmatch
import inspect
import os
import shutil
from datetime import datetime
from pathlib import Path

from lcode.worktree.git import _resolve_head_sha_from_fs, _run_git, warn
from lcode.worktree.manager import Manager, Worktree
from lcode.worktree.slug import flat_slug, validate_slug

_LOCAL_CONFIGS = (".lcode/config.yaml", ".lcode/settings.local.yaml")


async def create(
    manager: Manager, name: str, base_ref: str = "HEAD", manual: bool = False
) -> Worktree:
    validate_slug(name)
    async with manager.lock:
        if name in manager.active:
            raise ValueError(f"Worktree 已存在: {name}")

        flat = flat_slug(name)
        path = Path(manager.worktree_dir) / flat
        branch = f"worktree-{flat}"

        # 快速恢复：目录已存在时只读文件系统，不调用 git。
        if path.exists():
            head = _resolve_head_sha_from_fs(str(path))
            if head is None:
                raise RuntimeError(f"目录已存在但不是有效 Worktree: {path}")
            worktree = Worktree(
                name=name,
                path=str(path),
                branch=branch,
                based_on=base_ref,
                head_commit=head,
                created=datetime.now(),
                manual=manual,
            )
            manager.active[name] = worktree
            return worktree

        try:
            await _run_git(
                manager.repo_root,
                "worktree",
                "add",
                "-B",
                branch,
                str(path),
                base_ref,
            )
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise

        await _perform_post_creation_setup(manager.repo_root, path, manager.symlink_dirs)
        head = await _run_git(str(path), "rev-parse", "HEAD")
        worktree = Worktree(
            name=name,
            path=str(path),
            branch=branch,
            based_on=base_ref,
            head_commit=head,
            created=datetime.now(),
            manual=manual,
        )
        manager.active[name] = worktree
        return worktree


async def _perform_post_creation_setup(
    repo_root: str, wt_path: Path, symlink_dirs: list[str]
) -> None:
    steps = (
        ("copy-configs", lambda: copy_local_configs(repo_root, wt_path)),
        ("git-hooks", lambda: setup_git_hooks(repo_root, wt_path)),
        ("symlink", lambda: symlink_large_dirs(repo_root, wt_path, symlink_dirs)),
        ("include-ignored", lambda: copy_included_ignored(repo_root, wt_path)),
    )
    for label, step in steps:
        try:
            await _maybe_await(step())
        except Exception as exc:  # noqa: BLE001 - best-effort (N2)
            warn(f"setup {label}: {exc}")


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def copy_local_configs(repo_root: str, wt_path: Path) -> None:
    for relative in _LOCAL_CONFIGS:
        source = Path(repo_root) / relative
        if not source.is_file():
            continue
        target = Path(wt_path) / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


async def setup_git_hooks(repo_root: str, wt_path: Path) -> None:
    hooks: str | None = None
    husky = Path(repo_root) / ".husky"
    if husky.is_dir():
        hooks = str(husky.resolve())
    else:
        try:
            configured = await _run_git(repo_root, "config", "--get", "core.hooksPath")
        except Exception:  # noqa: BLE001 - not configured
            configured = ""
        if configured.strip():
            candidate = Path(configured.strip())
            if not candidate.is_absolute():
                candidate = Path(repo_root) / candidate
            hooks = str(candidate.resolve())
    if hooks:
        await _run_git(str(wt_path), "config", "core.hooksPath", hooks)


def symlink_large_dirs(repo_root: str, wt_path: Path, symlink_dirs: list[str]) -> None:
    for name in symlink_dirs:
        source = Path(repo_root) / name
        target = Path(wt_path) / name
        if not source.is_dir() or target.exists():
            continue
        os.symlink(source, target, target_is_directory=True)


async def copy_included_ignored(repo_root: str, wt_path: Path) -> None:
    patterns = _read_worktree_include(repo_root)
    if not patterns:
        return
    listed = await _run_git(
        repo_root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
    )
    for relative in listed.splitlines():
        relative = relative.strip()
        if not relative or relative.endswith("/"):
            continue
        name = Path(relative).name
        if not any(
            fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern)
            for pattern in patterns
        ):
            continue
        source = Path(repo_root) / relative
        target = Path(wt_path) / relative
        if not source.is_file() or target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            warn(f"copy included {relative}: {exc}")


def _read_worktree_include(repo_root: str) -> list[str]:
    path = Path(repo_root) / ".worktreeinclude"
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


__all__ = [
    "copy_included_ignored",
    "copy_local_configs",
    "create",
    "setup_git_hooks",
    "symlink_large_dirs",
]
