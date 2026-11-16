from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from mewcode.worktree import Manager
from mewcode.worktree.sweep import random_agent_name, sweep_stale


def test_random_agent_name_pattern():
    for _ in range(20):
        assert re.match(r"^agent-a[0-9a-f]{7}$", random_agent_name())


def _age(path: Path, days: int = 2) -> None:
    stamp = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


async def test_sweep_three_layer_filter(git_repo):
    manager = Manager(str(git_repo))
    clean = await manager.create("agent-a0a0a0a0", "HEAD", manual=False)
    changed = await manager.create("agent-ab1b2c30", "HEAD", manual=False)
    manual = await manager.create("demo", "HEAD", manual=True)

    (Path(changed.path) / "a.txt").write_text("dirty\n", encoding="utf-8")
    for worktree in (clean, changed, manual):
        _age(Path(worktree.path))

    removed = await sweep_stale(manager, datetime.now() - timedelta(hours=24))

    assert removed == ["agent-a0a0a0a0"]
    assert not Path(clean.path).exists()
    assert Path(changed.path).is_dir()
    assert Path(manual.path).is_dir()


async def test_sweep_skips_recent_and_current_session(git_repo):
    manager = Manager(str(git_repo))
    recent = await manager.create("agent-a11111110", "HEAD", manual=False)
    current = await manager.create("agent-a22222220", "HEAD", manual=False)
    _age(Path(current.path))
    await manager.enter("agent-a22222220")

    removed = await sweep_stale(manager, datetime.now() - timedelta(hours=24))
    assert removed == []
    assert Path(recent.path).is_dir()  # 未超过 cutoff
    assert Path(current.path).is_dir()  # 当前 session


async def test_sweep_skips_unpushed_commits(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("agent-a33333330", "HEAD", manual=False)
    (Path(worktree.path) / "new.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", worktree.path, "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", worktree.path, "commit", "-qm", "local"],
        check=True,
        capture_output=True,
    )
    _age(Path(worktree.path))

    removed = await sweep_stale(manager, datetime.now() - timedelta(hours=24))
    assert removed == []
    assert Path(worktree.path).is_dir()
