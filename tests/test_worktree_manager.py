from __future__ import annotations

import json
from pathlib import Path

import pytest

from mewcode.worktree import Manager, WorktreeSession
from mewcode.worktree.session import load_session, save_session


def test_construct_creates_worktree_dir(git_repo):
    manager = Manager(str(git_repo))
    assert Path(manager.worktree_dir).is_dir()
    assert manager.current_session() is None
    assert manager.list() == []


def test_construct_rejects_non_git_repo(tmp_path):
    with pytest.raises(ValueError):
        Manager(str(tmp_path))


def test_session_roundtrip_and_null(tmp_path):
    path = tmp_path / "session.json"
    session = WorktreeSession(
        original_cwd="/repo",
        worktree_path="/repo/.mewcode/worktrees/a",
        worktree_name="a",
        original_branch="main",
        original_head_commit="abc",
        session_id="sid",
    )
    save_session(path, session)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "original_cwd": "/repo",
        "worktree_path": "/repo/.mewcode/worktrees/a",
        "worktree_name": "a",
        "original_branch": "main",
        "original_head_commit": "abc",
        "session_id": "sid",
        "hook_based": False,
    }
    loaded = load_session(path)
    assert loaded == session

    save_session(path, None)
    assert path.read_text(encoding="utf-8") == "null"
    assert load_session(path) is None


async def test_session_persisted_and_reloaded(git_repo):
    manager = Manager(str(git_repo))
    await manager.create("alice", "HEAD", manual=True)
    await manager.enter("alice")
    session_id = manager.current_session().session_id
    assert session_id

    reloaded = Manager(str(git_repo))
    session = reloaded.current_session()
    assert session is not None
    assert session.worktree_name == "alice"
    assert session.session_id == session_id
    assert "alice" in reloaded.active


def test_missing_worktree_session_is_cleared(git_repo, capsys):
    manager = Manager(str(git_repo))
    path = Path(manager.session_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "original_cwd": "/x",
                "worktree_path": str(Path(manager.worktree_dir) / "gone"),
                "worktree_name": "gone",
                "original_branch": "main",
                "original_head_commit": "abc",
                "session_id": "sid",
            }
        ),
        encoding="utf-8",
    )
    reloaded = Manager(str(git_repo))
    assert reloaded.current_session() is None
    assert "session worktree gone, cleared" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "null"


def test_corrupt_session_is_cleared(git_repo, capsys):
    manager = Manager(str(git_repo))
    Path(manager.session_file).write_text("{not json", encoding="utf-8")
    reloaded = Manager(str(git_repo))
    assert reloaded.current_session() is None
    assert "损坏" in capsys.readouterr().err
