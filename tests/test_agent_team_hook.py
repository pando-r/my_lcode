from __future__ import annotations

import json

from mewcode.agent import Agent
from mewcode.agent.agent_tool import AgentTool
from mewcode.agent.team_hook import IncomingMessage, TeammateContext
from mewcode.agent.tool_context import ToolCallContext, bind_tool_context
from mewcode.conversation import Conversation
from mewcode.llm import StreamEvent
from mewcode.permission import Mode, new_engine
from mewcode.subagent import load_catalog
from mewcode.task import Manager as TaskManager
from mewcode.tool import Registry


class ScriptedProvider:
    def __init__(self, text="ok"):
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


class FakeTeamHook:
    def __init__(self):
        self.requests = []

    async def spawn_teammate(self, req):
        self.requests.append(req)
        return json.dumps({"member_name": req.member_name, "agent_id": "agent-x"})

    def is_teammate_context(self, ctx):
        teammate = getattr(ctx.agent, "teammate_context", None)
        if teammate is None:
            return "", "", False
        return (
            teammate.team_name,
            teammate.member_name,
            teammate.backend_type == "in-process",
        )


def _parent(tmp_path, provider):
    registry = Registry()
    return Agent(provider, registry, "dev", new_engine(str(tmp_path))[0])


async def test_agent_tool_team_branch_delegates(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    provider = ScriptedProvider()
    parent = _parent(tmp_path, provider)
    conv = Conversation()
    conv.add_user("parent")
    hook = FakeTeamHook()
    tool = AgentTool(load_catalog(str(tmp_path)), TaskManager(), team_hook=hook)
    with bind_tool_context(ToolCallContext(parent, conv)):
        result = await tool.execute(
            json.dumps(
                {
                    "prompt": "work",
                    "description": "d",
                    "team_name": "demo",
                    "name": "alice",
                    "subagent_type": "general-purpose",
                }
            )
        )
    assert result.is_error is False
    assert "agent-x" in result.content
    request = hook.requests[0]
    assert request.team_name == "demo"
    assert request.member_name == "alice"
    assert request.prompt == "work"
    assert request.parent_agent is parent


async def test_inprocess_teammate_cannot_spawn_team(tmp_path):
    parent = _parent(tmp_path, ScriptedProvider())
    parent._is_subagent = True
    teammate = TeammateContext(
        team_name="demo",
        member_name="alice",
        agent_id="agent-x",
        backend_type="in-process",
    )
    parent._teammate_context = teammate
    conv = Conversation()
    conv.add_user("x")
    hook = FakeTeamHook()
    tool = AgentTool(load_catalog(str(tmp_path)), TaskManager(), team_hook=hook)
    with bind_tool_context(ToolCallContext(parent, conv, is_subagent=True)):
        result = await tool.execute(
            json.dumps(
                {
                    "prompt": "work",
                    "description": "d",
                    "team_name": "demo",
                }
            )
        )
    assert result.is_error
    assert "InProcessTeammateNoSpawnError" in result.content
    assert hook.requests == []


async def test_mailbox_messages_injected_into_reminder(tmp_path):
    provider = ScriptedProvider("done")
    agent = Agent(
        provider,
        Registry(),
        "dev",
        new_engine(str(tmp_path))[0],
        teammate_context=TeammateContext(
            team_name="demo",
            member_name="alice",
            agent_id="a1",
        ),
    )
    marked: list[list[int]] = []

    async def read_unread():
        return [0], [
            IncomingMessage(
                from_="lead",
                text="please report",
                type="text",
                timestamp="t",
            )
        ]

    async def mark_read(indices):
        marked.append(indices)

    agent._teammate_context.read_unread = read_unread
    agent._teammate_context.mark_read = mark_read
    text = await agent.run_to_completion(Conversation(), "start")
    assert text == "done"
    assert "<incoming-messages>" in provider.requests[0].reminder
    assert "please report" in provider.requests[0].reminder
    assert marked == [[0]]


async def test_plan_approval_switches_permission_mode(tmp_path):
    provider = ScriptedProvider("done")
    agent = Agent(
        provider,
        Registry(),
        "dev",
        new_engine(str(tmp_path))[0],
        permission_mode=Mode.PLAN,
        teammate_context=TeammateContext(team_name="demo", member_name="alice", agent_id="a1"),
    )

    async def read_unread():
        return [0], [
            IncomingMessage(
                from_="lead",
                text="approved",
                type="plan_approval_response",
                approve=True,
            )
        ]

    async def mark_read(indices):
        return None

    agent._teammate_context.read_unread = read_unread
    agent._teammate_context.mark_read = mark_read
    await agent.run_to_completion(Conversation(), "continue")
    assert agent.permission_mode is Mode.DEFAULT
    assert "<incoming-messages>" in provider.requests[0].reminder
