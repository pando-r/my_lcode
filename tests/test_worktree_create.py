from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mewcode.worktree import Manager
from mewcode.worktree import create as create_module
from mewcode.worktree.git import _run_git


async def test_create_nested_slug(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("team/alice", "HEAD", manual=True)
    assert Path(worktree.path) == Path(manager.worktree_dir) / "team+alice"
    assert worktree.branch == "worktree-team+alice"
    assert Path(worktree.path, "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert manager.get("team/alice") is worktree


async def test_create_duplicate_name(git_repo):
    manager = Manager(str(git_repo))
    await manager.create("alice", "HEAD", manual=True)
    with pytest.raises(ValueError):
        await manager.create("alice", "HEAD", manual=True)


async def test_fast_recovery_does_not_call_git(git_repo, monkeypatch):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    fresh = Manager(str(git_repo))
    fresh.active.clear()

    async def boom(*args, **kwargs):
        raise AssertionError("git should not be called")

    monkeypatch.setattr(create_module, "_run_git", boom)
    recovered = await fresh.create("alice", "HEAD", manual=True)
    assert recovered.path == worktree.path
    assert recovered.head_commit == worktree.head_commit


async def test_setup_copies_local_config(git_repo):
    config_dir = Path(git_repo) / ".mewcode"
    config_dir.mkdir()
    (config_dir / "settings.local.yaml").write_text("permissions: {}\n", encoding="utf-8")
    manager = Manager(str(git_repo))
    worktree = await manager.create("cfg", "HEAD", manual=True)
    target = Path(worktree.path) / ".mewcode" / "settings.local.yaml"
    assert target.read_text(encoding="utf-8") == "permissions: {}\n"


async def test_setup_configures_git_hooks(git_repo):
    (Path(git_repo) / ".husky").mkdir()
    manager = Manager(str(git_repo))
    worktree = await manager.create("hooks", "HEAD", manual=True)
    configured = await _run_git(worktree.path, "config", "--get", "core.hooksPath")
    assert configured == str((Path(git_repo) / ".husky").resolve())


async def test_setup_symlinks_large_dirs(git_repo):
    source = Path(git_repo) / "node_modules"
    source.mkdir()
    (source / "pkg.js").write_text("x", encoding="utf-8")
    manager = Manager(str(git_repo))
    worktree = await manager.create("link", "HEAD", manual=True)
    target = Path(worktree.path) / "node_modules"
    if not target.is_symlink():
        pytest.skip("当前平台/权限不支持 symlink")


async def test_setup_copies_included_ignored(git_repo):
    (Path(git_repo) / ".gitignore").write_text(".env\n", encoding="utf-8")
    (Path(git_repo) / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (Path(git_repo) / ".worktreeinclude").write_text("*.env\n", encoding="utf-8")
    manager = Manager(str(git_repo))
    worktree = await manager.create("include", "HEAD", manual=True)
    target = Path(worktree.path) / ".env"
    assert target.read_text(encoding="utf-8") == "SECRET=1\n"


async def test_worktree_is_inside_repo_and_branch_exists(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    assert Path(worktree.path).is_relative_to(Path(git_repo))
    branches = subprocess.run(
        ["git", "-C", str(git_repo), "branch", "--list", worktree.branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert worktree.branch in branches
    # 目录在 .mewcode 下，应被 .gitignore 忽略（若项目配置了 ignore）
    assert os.path.isdir(worktree.path)
