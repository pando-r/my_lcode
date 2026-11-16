from __future__ import annotations

import json
from pathlib import Path

import pytest

from mewcode.team import (
    AgentNameRegistry,
    BackendType,
    Manager,
    TeamHasActiveMembersError,
    TeammateInfo,
)
from mewcode.team.persistence import read_json
from mewcode.team.types import Team


def _manager(tmp_path, git_repo):
    return Manager(tmp_path / "home", git_repo)


async def test_create_and_get(tmp_path, git_repo, monkeypatch):
    monkeypatch.setenv("TMUX", "")
    monkeypatch.setattr("shutil.which", lambda name: None)
    manager = _manager(tmp_path, git_repo)
    team = await manager.create("refactor auth", "desc")
    assert team.sanitized_name == "refactor-auth"
    assert team.backend is BackendType.IN_PROCESS
    config = Path(team.config_path)
    assert config.is_file()
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["sanitized_name"] == "refactor-auth"
    assert data["members"][0]["name"] == "lead"
    assert manager.get("refactor-auth") is team
    assert manager.get("refactor auth") is team


async def test_duplicate_suffix(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    first = await manager.create("demo")
    second = await manager.create("demo")
    assert first.sanitized_name == "demo"
    assert second.sanitized_name == "demo-2"


async def test_delete_active_members_requires_force(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    team = await manager.create("demo")
    await team.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=None))
    with pytest.raises(TeamHasActiveMembersError):
        await manager.delete("demo", False)
    assert Path(team.config_dir).is_dir()

    await team.set_member_active("alice", False)
    await manager.delete("demo", False)
    assert not Path(team.config_dir).exists()
    assert manager.get("demo") is None


async def test_member_operations_and_reload(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    team = await manager.create("demo")
    await team.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=None))
    data = read_json(team.config_path)
    assert [m["name"] for m in data["members"]] == ["lead", "alice"]

    await team.set_member_active("alice", False)
    data = read_json(team.config_path)
    alice = next(m for m in data["members"] if m["name"] == "alice")
    assert alice["is_active"] is False

    await team.remove_member("alice")
    data = read_json(team.config_path)
    assert [m["name"] for m in data["members"]] == ["lead"]


async def test_cross_process_reload_before_set_active(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    team = await manager.create("demo")
    # 另一个进程读到的旧快照（无 alice）
    stale = Team.from_dict(read_json(team.config_path), team.config_dir)
    # Lead 进程写入了 alice
    await team.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=None))
    # stale 对象执行 set_member_active 必须先 reload，否则会 no-op
    await stale.set_member_active("alice", False)
    data = read_json(team.config_path)
    alice = next(m for m in data["members"] if m["name"] == "alice")
    assert alice["is_active"] is False


async def test_scan_restores_teams_and_skips_corrupt(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    await manager.create("demo")
    bad = Path(tmp_path / "home" / ".mewcode" / "teams" / "bad")
    bad.mkdir()
    (bad / "config.json").write_text("{broken", encoding="utf-8")

    reloaded = _manager(tmp_path, git_repo)
    assert reloaded.get("demo") is not None
    assert reloaded.get("bad") is None


async def test_handle_task_done_writes_idle(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    manager.registry = AgentNameRegistry()
    team = await manager.create("demo")
    await team.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=True))
    manager.registry.register("alice", "a1")

    await manager.handle_task_done("a1")

    reloaded = Team.from_dict(read_json(team.config_path), team.config_dir)
    assert reloaded.member_by_name("alice").is_active is False
    from mewcode.team.mailbox import Box

    messages = await Box(team.mailbox_dir).read("lead")
    assert any("[idle] alice" in message.text for message in messages)

    lead_mail = await manager.poll_lead_mailboxes()
    assert any(message.from_ == "alice" for message in lead_mail)
    # 已读后不再返回
    assert await manager.poll_lead_mailboxes() == []
