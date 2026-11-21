from __future__ import annotations

import asyncio
import json

from lcode.agent import Agent
from lcode.agent.agent_tool import AgentTool
from lcode.conversation import Conversation
from lcode.llm import StreamEvent, ToolCall
from lcode.permission import Mode, new_engine
from lcode.subagent import load_catalog
from lcode.task import Manager as TaskManager
from lcode.task import (
    SendMessageTool as BackgroundSendTool,
)
from lcode.task import (
    TaskGetTool as BackgroundGetTool,
)
from lcode.task import (
    TaskListTool as BackgroundListTool,
)
from lcode.team import AgentNameRegistry, BackendType, Manager
from lcode.team.tools import team_tools
from lcode.tool import new_default_registry
from lcode.worktree import Manager as WorktreeManager


class ScriptedProvider:
    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = 0
        self.requests = []

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    async def stream(self, req):
        self.requests.append(req)
        idx = min(self.calls, len(self.scripts) - 1)
        self.calls += 1
        for event in self.scripts[idx]:
            yield event


def _tool_call(name: str, args: dict, call_id: str = "c1"):
    return [
        StreamEvent(tool_calls=[ToolCall(id=call_id, name=name, input=json.dumps(args))]),
        StreamEvent(done=True),
    ]


async def test_main_agent_creates_team_and_spawns_member(git_repo, tmp_path, monkeypatch):
    import lcode.team.manager as team_manager_module

    monkeypatch.setattr(team_manager_module, "detect", lambda: BackendType.IN_PROCESS)
    task_mgr = TaskManager()
    name_reg = AgentNameRegistry()
    task_mgr.set_name_registry(name_reg)
    team_mgr = Manager(
        tmp_path / "home",
        git_repo,
        wt_mgr=WorktreeManager(str(git_repo)),
        task_mgr=task_mgr,
        name_reg=name_reg,
        catalog=load_catalog(str(git_repo)),
    )
    task_mgr.on_task_done(team_mgr.handle_task_done)

    provider = ScriptedProvider(
        [
            _tool_call("TeamCreate", {"team_name": "demo"}, "t1"),
            _tool_call(
                "Agent",
                {
                    "prompt": "member task",
                    "description": "d",
                    "team_name": "demo",
                    "name": "alice",
                },
                "a1",
            ),
            [StreamEvent(text="ok"), StreamEvent(done=True)],
            [StreamEvent(text="finished"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    agent_tool = AgentTool(
        load_catalog(str(git_repo)),
        task_mgr,
        bg_enabled=True,
        worktree_mgr=team_mgr.wt_mgr,
        team_hook=team_mgr,
    )
    registry.register(agent_tool)
    for tool in team_tools(
        team_mgr,
        BackgroundListTool(task_mgr),
        BackgroundGetTool(task_mgr),
        BackgroundSendTool(task_mgr),
    ):
        registry.register(tool)

    parent = Agent(provider, registry, "dev", new_engine(str(git_repo))[0])
    conv = Conversation()
    conv.add_user("make a team")
    async for _ in parent.run(conv, Mode.BYPASS, asyncio.Event()):
        pass

    for _ in range(100):
        if task_mgr.list() and task_mgr.list()[0].status.name != "RUNNING":
            break
        await asyncio.sleep(0.02)
    team = team_mgr.get("demo")
    assert team is not None
    member = team.member_by_name("alice")
    assert member is not None
    assert member.agent_type == "general-purpose"
    tool_messages = [m for m in conv.messages() if m.role == "tool"]
    assert "member_name" in tool_messages[-1].tool_results[-1].content

    teammate_tools = [
        {tool.name for tool in request.tools}
        for request in provider.requests
        if "agent in a team" in request.system.stable
    ]
    assert teammate_tools
    assert "TeamCreate" not in teammate_tools[0]
    assert "TaskUpdate" in teammate_tools[0]
    assert "Agent" in teammate_tools[0]

    await team_mgr.delete("demo", force=True)
