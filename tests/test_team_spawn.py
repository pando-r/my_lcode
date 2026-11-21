from __future__ import annotations

import asyncio
import json
from pathlib import Path

from lcode.agent import Agent
from lcode.agent.team_hook import TeamSpawnRequest
from lcode.conversation import Conversation
from lcode.llm import StreamEvent, ToolCall
from lcode.permission import new_engine
from lcode.subagent import load_catalog
from lcode.task import Manager as TaskManager
from lcode.team import AgentNameRegistry, BackendType, Manager
from lcode.team.types import Team
from lcode.tool import Registry
from lcode.worktree import Manager as WorktreeManager


class MultiProvider:
    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = 0

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    async def stream(self, req):
        idx = min(self.calls, len(self.scripts) - 1)
        self.calls += 1
        for event in self.scripts[idx]:
            yield event


class ScriptedProvider:
    def __init__(self, text="member done"):
        self.text = text
        self.requests = []

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    async def stream(self, req):
        self.requests.append(req)
        yield StreamEvent(text=self.text)
        yield StreamEvent(done=True)


async def test_spawn_inprocess_member_full_cycle(git_repo, tmp_path, monkeypatch):
    import lcode.team.manager as team_manager_module

    monkeypatch.setattr(team_manager_module, "detect", lambda: BackendType.IN_PROCESS)
    home = tmp_path / "home"
    task_mgr = TaskManager()
    name_reg = AgentNameRegistry()
    task_mgr.set_name_registry(name_reg)
    wt_mgr = WorktreeManager(str(git_repo))
    parent_provider = ScriptedProvider()
    registry = Registry()
    parent = Agent(parent_provider, registry, "dev", new_engine(str(git_repo))[0])
    manager = Manager(
        home,
        git_repo,
        wt_mgr=wt_mgr,
        task_mgr=task_mgr,
        name_reg=name_reg,
        catalog=load_catalog(str(git_repo)),
    )
    task_mgr.on_task_done(manager.handle_task_done)
    team = await manager.create("demo")
    assert team.backend is BackendType.IN_PROCESS

    conv = Conversation()
    conv.add_user("lead context")
    result = await manager.spawn_teammate(
        TeamSpawnRequest(
            team_name="demo",
            member_name="alice",
            agent_type="general-purpose",
            prompt="do the work",
            parent_agent=parent,
            parent_conv=conv,
        )
    )
    payload = json.loads(result)
    assert payload["member_name"] == "alice"
    assert payload["backend"] == "in-process"
    assert Path(payload["worktree"]).is_dir()
    assert "team-demo+alice" in payload["worktree"]

    await asyncio.wait_for(task_mgr.subscribe_done().get(), timeout=3)
    await asyncio.sleep(0.05)

    refreshed = Team.from_dict(
        json.loads(Path(team.config_path).read_text(encoding="utf-8")),
        team.config_dir,
    )
    member = refreshed.member_by_name("alice")
    assert member is not None
    assert member.is_active is False
    assert Path(member.session_dir).is_dir()
    assert name_reg.resolve("alice") == payload["agent_id"]

    from lcode.team.mailbox import Box

    lead_messages = await Box(team.mailbox_dir).read("lead")
    assert any("[idle] alice" in message.text for message in lead_messages)

    request = parent_provider.requests[0]
    assert "agent in a team" in request.system.stable
    assert "<team-context>" in request.reminder

    session_dir = member.session_dir
    await manager.delete("demo", force=True)
    assert manager.get("demo") is None
    assert not Path(payload["worktree"]).exists()
    assert not Path(team.config_dir).exists()
    assert not Path(session_dir).exists()


async def test_plan_mode_required_member_starts_in_plan(git_repo, tmp_path, monkeypatch):
    import lcode.team.manager as team_manager_module

    monkeypatch.setattr(team_manager_module, "detect", lambda: BackendType.IN_PROCESS)
    agent_file = git_repo / ".lcode" / "agents" / "planner.md"
    agent_file.parent.mkdir(parents=True, exist_ok=True)
    agent_file.write_text(
        "---\nname: planner\ndescription: d\nplan_mode_required: true\n---\nplan",
        encoding="utf-8",
    )
    task_mgr = TaskManager()
    parent = Agent(ScriptedProvider(), Registry(), "dev", new_engine(str(git_repo))[0])
    manager = Manager(
        tmp_path / "home5",
        git_repo,
        wt_mgr=WorktreeManager(str(git_repo)),
        task_mgr=task_mgr,
        catalog=load_catalog(str(git_repo)),
    )
    await manager.create("demo")
    payload = json.loads(
        await manager.spawn_teammate(
            TeamSpawnRequest(
                team_name="demo",
                member_name="planner",
                agent_type="planner",
                prompt="plan it",
                parent_agent=parent,
                parent_conv=Conversation(),
            )
        )
    )
    from lcode.permission import Mode

    task = task_mgr.get(payload["agent_id"])
    assert task.sub_agent.permission_mode is Mode.PLAN
    await asyncio.wait_for(task_mgr.subscribe_done().get(), timeout=3)
    await manager.delete("demo", force=True)


async def test_send_message_resumes_stopped_inprocess_member(git_repo, tmp_path, monkeypatch):
    import lcode.team.manager as team_manager_module
    from lcode.team.tools import TeamAwareSendMessageTool

    monkeypatch.setattr(team_manager_module, "detect", lambda: BackendType.IN_PROCESS)
    task_mgr = TaskManager()
    name_reg = AgentNameRegistry()
    task_mgr.set_name_registry(name_reg)
    provider = MultiProvider(
        [
            [StreamEvent(text="one"), StreamEvent(done=True)],
            [StreamEvent(text="two"), StreamEvent(done=True)],
        ]
    )
    parent = Agent(provider, Registry(), "dev", new_engine(str(git_repo))[0])
    manager = Manager(
        tmp_path / "home4",
        git_repo,
        wt_mgr=WorktreeManager(str(git_repo)),
        task_mgr=task_mgr,
        name_reg=name_reg,
        catalog=load_catalog(str(git_repo)),
    )
    await manager.create("demo")
    payload = json.loads(
        await manager.spawn_teammate(
            TeamSpawnRequest(
                team_name="demo",
                member_name="alice",
                prompt="first",
                parent_agent=parent,
                parent_conv=Conversation(),
            )
        )
    )
    await asyncio.wait_for(task_mgr.subscribe_done().get(), timeout=3)
    await asyncio.sleep(0.05)
    assert task_mgr.get(payload["agent_id"]).status.name == "COMPLETED"

    result = await TeamAwareSendMessageTool(manager, None).execute(
        json.dumps({"to": "alice", "content": "second"})
    )
    assert not result.is_error
    await asyncio.wait_for(task_mgr.subscribe_done().get(), timeout=3)
    task = task_mgr.get(payload["agent_id"])
    assert task.status.name == "COMPLETED"
    assert task.result == "two"
    assert provider.calls == 2
    await manager.delete("demo", force=True)


async def test_pane_member_initial_prompt_written_to_mailbox(git_repo, tmp_path, monkeypatch):
    import lcode.team.manager as team_manager_module
    import lcode.team.spawn as spawn_module

    monkeypatch.setattr(team_manager_module, "detect", lambda: BackendType.TMUX)

    class FakeBackend:
        def __init__(self):
            self.requests = []

        async def spawn(self, req):
            self.requests.append(req)
            return "%7", req.agent_id

        async def wake(self, pane_id, agent_id):
            return None

        async def kill(self, pane_id, agent_id):
            return None

    backend = FakeBackend()
    monkeypatch.setattr(spawn_module, "new_backend", lambda *a, **k: backend)

    task_mgr = TaskManager()
    parent = Agent(ScriptedProvider(), Registry(), "dev", new_engine(str(git_repo))[0])
    manager = Manager(
        tmp_path / "home3",
        git_repo,
        wt_mgr=WorktreeManager(str(git_repo)),
        task_mgr=task_mgr,
        catalog=load_catalog(str(git_repo)),
    )
    team = await manager.create("demo")
    payload = json.loads(
        await manager.spawn_teammate(
            TeamSpawnRequest(
                team_name="demo",
                member_name="alice",
                prompt="initial task",
                parent_agent=parent,
                parent_conv=Conversation(),
            )
        )
    )
    assert backend.requests
    from lcode.team.mailbox import Box

    messages = await Box(team.mailbox_dir).read(payload["agent_id"])
    assert messages[0].from_ == "lead"
    assert messages[0].text == "initial task"

    refreshed = Team.from_dict(
        json.loads(Path(team.config_path).read_text(encoding="utf-8")),
        team.config_dir,
    )
    member = refreshed.member_by_name("alice")
    assert member.pane_id == "%7"
    assert member.backend_type is BackendType.TMUX


async def test_inprocess_member_tools_use_worktree_cwd(git_repo, tmp_path, monkeypatch):
    import lcode.team.manager as team_manager_module
    from lcode.tool.write_file import WriteFileTool

    monkeypatch.setattr(team_manager_module, "detect", lambda: BackendType.IN_PROCESS)
    task_mgr = TaskManager()
    name_reg = AgentNameRegistry()
    task_mgr.set_name_registry(name_reg)
    wt_mgr = WorktreeManager(str(git_repo))
    provider = MultiProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="w1",
                            name="write_file",
                            input=json.dumps({"path": "member-only.txt", "content": "hi"}),
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(WriteFileTool())
    parent = Agent(provider, registry, "dev", new_engine(str(git_repo))[0])
    manager = Manager(
        tmp_path / "home2",
        git_repo,
        wt_mgr=wt_mgr,
        task_mgr=task_mgr,
        name_reg=name_reg,
        catalog=load_catalog(str(git_repo)),
    )
    await manager.create("demo")
    payload = json.loads(
        await manager.spawn_teammate(
            TeamSpawnRequest(
                team_name="demo",
                member_name="alice",
                prompt="write",
                parent_agent=parent,
                parent_conv=Conversation(),
            )
        )
    )
    await asyncio.wait_for(task_mgr.subscribe_done().get(), timeout=3)
    assert (Path(payload["worktree"]) / "member-only.txt").read_text(encoding="utf-8") == "hi"
    assert not (git_repo / "member-only.txt").exists()
