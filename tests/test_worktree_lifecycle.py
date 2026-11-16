from __future__ import annotations

import os
from pathlib import Path

import pytest

from mewcode.worktree import (
    ExitAction,
    ExitOptions,
    Manager,
    WorktreeHasChangesError,
)


async def test_enter_does_not_change_process_cwd(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    before = Path.cwd()
    session = await manager.enter("alice")
    assert Path.cwd() == before
    assert session.original_cwd == str(before)
    assert session.worktree_path == worktree.path
    assert session.worktree_name == "alice"
    assert session.session_id
    assert manager.current_session() is session
    assert Path(manager.session_file).is_file()


async def test_exit_remove_protects_changes(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    await manager.enter("alice")
    (Path(worktree.path) / "a.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(WorktreeHasChangesError):
        await manager.exit("alice", ExitAction.REMOVE, ExitOptions())
    assert Path(worktree.path).is_dir()

    report = await manager.exit("alice", ExitAction.REMOVE, ExitOptions(discard_changes=True))
    assert report.removed is True
    assert Path(worktree.path).exists() is False
    assert manager.current_session() is None
    assert Path(manager.session_file).read_text(encoding="utf-8") == "null"


async def test_exit_keep_preserves_worktree(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    await manager.enter("alice")
    report = await manager.exit("alice", ExitAction.KEEP, ExitOptions())
    assert report.removed is False
    assert Path(worktree.path).is_dir()
    assert manager.current_session() is None
    assert manager.get("alice") is not None


async def test_exit_only_current_session(git_repo):
    manager = Manager(str(git_repo))
    await manager.create("alice", "HEAD", manual=True)
    with pytest.raises(ValueError):
        await manager.exit("alice", ExitAction.KEEP, ExitOptions())


async def test_exit_restores_original_cwd(git_repo, tmp_path):
    manager = Manager(str(git_repo))
    await manager.create("alice", "HEAD", manual=True)
    original = Path.cwd()
    await manager.enter("alice")
    os.chdir(tmp_path)
    try:
        await manager.exit("alice", ExitAction.KEEP, ExitOptions())
        assert Path.cwd() == original
    finally:
        os.chdir(original)


async def test_remove_non_current(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    await manager.remove("alice", ExitOptions(discard_changes=True))
    assert manager.get("alice") is None
    assert not Path(worktree.path).exists()


async def test_remove_protects_changes(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    (Path(worktree.path) / "a.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(WorktreeHasChangesError):
        await manager.remove("alice", ExitOptions())


async def test_auto_cleanup_manual_keeps(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("manual-one", "HEAD", manual=True)
    report = await manager.auto_cleanup("manual-one")
    assert report.kept is True
    assert report.path == worktree.path
    assert Path(worktree.path).is_dir()


async def test_auto_cleanup_clean_removes(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("temp", "HEAD", manual=False)
    report = await manager.auto_cleanup("temp")
    assert report.kept is False
    assert not Path(worktree.path).exists()
    assert manager.get("temp") is None


async def test_auto_cleanup_changed_keeps(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("temp", "HEAD", manual=False)
    (Path(worktree.path) / "a.txt").write_text("changed\n", encoding="utf-8")
    report = await manager.auto_cleanup("temp")
    assert report.kept is True
    assert report.branch == worktree.branch
    assert Path(worktree.path).is_dir()
