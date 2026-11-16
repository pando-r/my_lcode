from __future__ import annotations

import pytest

from mewcode.team.backend import SpawnRequest, build_member_command
from mewcode.team.backend.detect import detect
from mewcode.team.backend.inprocess import InProcessBackend
from mewcode.team.backend.iterm2 import Iterm2Backend
from mewcode.team.backend.tmux import TmuxBackend
from mewcode.team.types import BackendType


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)


def test_detect_tmux_env(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux")
    assert detect() is BackendType.TMUX


def test_detect_iterm2(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/it2" if name == "it2" else None
    )
    assert detect() is BackendType.ITERM2


def test_detect_tmux_binary(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    assert detect() is BackendType.TMUX


def test_detect_in_process(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert detect() is BackendType.IN_PROCESS


def _request() -> SpawnRequest:
    return SpawnRequest(
        team_name="demo",
        member_name="alice",
        agent_id="agent-a1b2c3d",
        worktree_path="/repo/.mewcode/worktrees/team-demo+alice",
        session_dir="/repo/.mewcode/sessions/s1",
        agent_type="general-purpose",
        model="haiku",
        plan_mode_required=True,
    )


def test_member_command_contains_required_flags():
    command = build_member_command(_request())
    assert "--team-member" in command
    assert "--team demo" in command
    assert "--member alice" in command
    assert "--agent-id agent-a1b2c3d" in command
    assert "--session-dir /repo/.mewcode/sessions/s1" in command
    assert "--worktree /repo/.mewcode/worktrees/team-demo+alice" in command
    assert "--agent-type general-purpose" in command
    assert "--model haiku" in command
    assert "--plan-mode" in command


async def test_tmux_backend_commands(monkeypatch):
    calls: list[tuple] = []

    async def fake_run(*args):
        calls.append(args)
        return "%5"

    monkeypatch.setattr("mewcode.team.backend.tmux._run_tmux", fake_run)
    monkeypatch.setenv("TMUX", "/tmp/tmux")
    backend = TmuxBackend()
    pane, agent_id = await backend.spawn(_request())
    assert pane == "%5"
    assert agent_id == "agent-a1b2c3d"
    assert calls[0][0] == "split-window"
    assert "--team-member" in calls[0][-1]

    await backend.wake("%5", "agent-a1b2c3d")
    assert calls[1][:3] == ("send-keys", "-t", "%5")
    await backend.kill("%5", "agent-a1b2c3d")
    assert calls[2][:3] == ("kill-pane", "-t", "%5")


async def test_tmux_backend_new_session(monkeypatch):
    calls: list[tuple] = []

    async def fake_run(*args):
        calls.append(args)
        return "%9"

    monkeypatch.setattr("mewcode.team.backend.tmux._run_tmux", fake_run)
    backend = TmuxBackend()
    pane, _ = await backend.spawn(_request())
    assert pane == "%9"
    assert calls[0][0] == "new-session"


async def test_iterm2_backend_commands(monkeypatch):
    calls: list[tuple] = []

    async def fake_run(*args):
        calls.append(args)
        return "pane-1"

    monkeypatch.setattr("mewcode.team.backend.iterm2._run_it2", fake_run)
    backend = Iterm2Backend()
    pane, agent_id = await backend.spawn(_request())
    assert pane == "pane-1" and agent_id == "agent-a1b2c3d"
    assert calls[0][:3] == ("split", "--new-pane", "--command")
    await backend.wake("pane-1", agent_id)
    assert calls[1][0] == "send-text"
    await backend.kill("pane-1", agent_id)
    assert calls[2][0] == "close-pane"


async def test_inprocess_backend(monkeypatch):
    class FakeTaskManager:
        def __init__(self):
            self.launched = None
            self.stopped = None

        async def launch(self, agent, conv, name, task, task_id=None, cwd=""):
            self.launched = (agent, conv, name, task, task_id, cwd)
            return task_id

        async def stop(self, agent_id):
            self.stopped = agent_id

    task_mgr = FakeTaskManager()
    backend = InProcessBackend(task_mgr)
    request = _request()
    request.sub_agent = object()
    request.conv = object()
    pane, agent_id = await backend.spawn(request)
    assert pane == ""
    assert agent_id == "agent-a1b2c3d"
    assert task_mgr.launched[4] == "agent-a1b2c3d"
    assert task_mgr.launched[5] == "/repo/.mewcode/worktrees/team-demo+alice"
    await backend.wake("", agent_id)
    await backend.kill("", agent_id)
    assert task_mgr.stopped == agent_id
