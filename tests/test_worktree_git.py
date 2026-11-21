from __future__ import annotations

import subprocess

from lcode.worktree import Manager
from lcode.worktree.git import (
    _has_unpushed_commits,
    _has_worktree_changes,
    _resolve_head_sha_from_fs,
    _run_git,
)


async def test_run_git(git_repo):
    head = await _run_git(str(git_repo), "rev-parse", "HEAD")
    assert len(head) == 40


async def test_has_worktree_changes(git_repo):
    head = await _run_git(str(git_repo), "rev-parse", "HEAD")
    assert await _has_worktree_changes(str(git_repo), head) is False

    (git_repo / "a.txt").write_text("modified\n", encoding="utf-8")
    assert await _has_worktree_changes(str(git_repo), head) is True


async def test_has_worktree_changes_fail_closed(git_repo):
    assert await _has_worktree_changes(str(git_repo), "not-a-commit") is True


async def test_resolve_head_sha_from_worktree(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    expected = await _run_git(worktree.path, "rev-parse", "HEAD")
    assert _resolve_head_sha_from_fs(worktree.path) == expected


def test_resolve_head_sha_missing_path(tmp_path):
    assert _resolve_head_sha_from_fs(str(tmp_path)) is None


async def test_unpushed_commits(git_repo):
    # 无 remote 时，本地 commit 都算未推送。
    (git_repo / "b.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "second"], check=True)
    assert await _has_unpushed_commits(str(git_repo)) is True
