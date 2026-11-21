from __future__ import annotations

import asyncio
import json
from pathlib import Path

from lcode.agent import Agent
from lcode.agent.agent_tool import AgentTool
from lcode.agent.tool_context import ToolCallContext, bind_tool_context
from lcode.conversation import Conversation
from lcode.llm import StreamEvent
from lcode.permission import new_engine
from lcode.subagent import load_catalog
from lcode.task import Manager, Status
from lcode.tool import Registry, Result


class ScriptedProvider:
    def __init__(self, script, delay: float = 0.0) -> None:
        self.script = script
        self.delay = delay
        self.received = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.received.append(req)
        if self.delay:
            await asyncio.sleep(self.delay)
        for event in self.script:
            yield event


class FakeTool:
    def __init__(self, tool_name: str, read_only: bool = True) -> None:
        self._name = tool_name
        self.read_only = read_only

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return self._name

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        return Result(content="ok")


class FailingProvider:
    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        raise RuntimeError("provider down")
        yield  # pragma: no cover


def _isolate(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)


def _write_agent(tmp_path, name="demo", extra="") -> None:
    path = tmp_path / ".lcode" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: demo role\n{extra}---\nSUBAGENT SYSTEM",
        encoding="utf-8",
    )


def _setup(tmp_path, monkeypatch, provider, *, bg_enabled=True, extra="", agent_name="demo"):
    _isolate(tmp_path, monkeypatch)
    _write_agent(tmp_path, agent_name, extra)
    catalog = load_catalog(tmp_path)
    manager = Manager()
    registry = Registry()
    registry.register(FakeTool("read_file"))
    registry.register(FakeTool("write_file", read_only=False))
    parent = Agent(provider, registry, "dev", new_engine(str(tmp_path))[0])
    tool = AgentTool(catalog, manager, parent=parent, bg_enabled=bg_enabled)
    conv = Conversation()
    conv.add_user("parent task")
    ctx = ToolCallContext(parent, conv)
    return tool, manager, registry, conv, ctx


def _args(**kwargs) -> str:
    data = {"prompt": "do work", "description": "desc"}
    data.update(kwargs)
    return json.dumps(data)


async def test_missing_required_fields(tmp_path, monkeypatch):
    tool, *_ = _setup(tmp_path, monkeypatch, ScriptedProvider([]))
    result = await tool.execute("{}")
    assert result.is_error and "prompt" in result.content
    result = await tool.execute(json.dumps({"prompt": "x"}))
    assert result.is_error and "description" in result.content


async def test_unknown_subagent_type(tmp_path, monkeypatch):
    tool, *_ = _setup(tmp_path, monkeypatch, ScriptedProvider([]))
    result = await tool.execute(_args(subagent_type="ghost"))
    assert result.is_error
    assert "未知 subagent_type" in result.content


async def test_definition_subagent_returns_final_text(tmp_path, monkeypatch):
    provider = ScriptedProvider([StreamEvent(text="sub-result"), StreamEvent(done=True)])
    tool, manager, registry, conv, ctx = _setup(
        tmp_path,
        monkeypatch,
        provider,
        extra="disallowedTools: [write_file]\nmaxTurns: 3\npermissionMode: dontAsk\n",
    )
    with bind_tool_context(ctx):
        result = await tool.execute(_args(subagent_type="demo"))

    assert result.is_error is False
    assert result.content == "sub-result"
    request = provider.received[0]
    tool_names = [definition.name for definition in request.tools]
    assert "Agent" not in tool_names
    assert "write_file" not in tool_names
    assert request.system.stable == "SUBAGENT SYSTEM"


async def test_tool_schema_and_description(tmp_path, monkeypatch):
    tool, *_ = _setup(tmp_path, monkeypatch, ScriptedProvider([]))
    assert tool.name() == "Agent"
    schema = tool.parameters()
    assert set(schema["properties"]) == {
        "prompt",
        "description",
        "subagent_type",
        "model",
        "run_in_background",
        "name",
        "team_name",
    }
    assert schema["required"] == ["prompt", "description"]
    assert "demo" in tool.description()


async def test_explicit_background_returns_task_id(tmp_path, monkeypatch):
    provider = ScriptedProvider([StreamEvent(text="background result"), StreamEvent(done=True)])
    tool, manager, *_rest = _setup(tmp_path, monkeypatch, provider)
    ctx = _rest[-1]
    with bind_tool_context(ctx):
        result = await tool.execute(_args(subagent_type="demo", run_in_background=True))
    data = json.loads(result.content)
    assert data["status"] == "async_launched"

    await asyncio.wait_for(manager.subscribe_done().get(), timeout=2)
    assert manager.get(data["task_id"]).status is Status.COMPLETED
    assert manager.get(data["task_id"]).result == "background result"


async def test_nested_subagent_is_blocked(tmp_path, monkeypatch):
    tool, manager, registry, conv, ctx = _setup(tmp_path, monkeypatch, ScriptedProvider([]))
    nested = ToolCallContext(ctx.agent, ctx.conv, is_subagent=True)
    with bind_tool_context(nested):
        result = await tool.execute(_args(subagent_type="demo"))
    assert result.is_error and "cannot spawn Agent" in result.content


async def test_fork_boilerplate_context_is_blocked(tmp_path, monkeypatch):
    from lcode.agent.fork import FORK_BOILERPLATE

    tool, *_rest = _setup(tmp_path, monkeypatch, ScriptedProvider([]))
    ctx = _rest[-1]
    ctx.conv.add_user(FORK_BOILERPLATE + "nested")
    with bind_tool_context(ctx):
        result = await tool.execute(_args())
    assert result.is_error
    assert "Fork 子 Agent 不能再启动 Agent" in result.content


async def test_background_disabled_rejects_explicit_and_fork(tmp_path, monkeypatch):
    tool, *_rest = _setup(tmp_path, monkeypatch, ScriptedProvider([]), bg_enabled=False)
    ctx = _rest[-1]
    with bind_tool_context(ctx):
        explicit = await tool.execute(_args(subagent_type="demo", run_in_background=True))
    assert explicit.is_error and "后台" in explicit.content

    with bind_tool_context(ctx):
        fork = await tool.execute(_args())
    assert fork.is_error and "Fork" in fork.content


async def test_fork_path_clones_and_keeps_agent_tool(tmp_path, monkeypatch):
    provider = ScriptedProvider([StreamEvent(text="forked"), StreamEvent(done=True)])
    tool, manager, registry, conv, ctx = _setup(tmp_path, monkeypatch, provider)
    registry.register(FakeTool("Agent", read_only=False))

    with bind_tool_context(ctx):
        result = await tool.execute(_args(prompt="child task"))
    data = json.loads(result.content)
    assert data["status"] == "async_launched"

    await asyncio.wait_for(manager.subscribe_done().get(), timeout=2)
    task = manager.get(data["task_id"])
    assert task.status is Status.COMPLETED
    contents = [message.content for message in task.conv.messages()]
    assert contents[0] == "parent task"
    assert any("<fork_boilerplate>" in content for content in contents)
    assert any(content.endswith("child task") for content in contents)
    request_tools = [definition.name for definition in provider.received[0].tools]
    assert "Agent" in request_tools


async def test_inline_timeout_switches_to_background(tmp_path, monkeypatch):
    import lcode.agent.agent_tool as agent_tool_mod

    monkeypatch.setattr(agent_tool_mod, "AUTO_BACKGROUND_SECONDS", 0.05)
    provider = ScriptedProvider(
        [StreamEvent(text="slow result"), StreamEvent(done=True)], delay=0.2
    )
    tool, manager, *_rest = _setup(tmp_path, monkeypatch, provider)
    ctx = _rest[-1]
    with bind_tool_context(ctx):
        result = await tool.execute(_args(subagent_type="demo"))
    data = json.loads(result.content)
    assert data["status"] == "timed_out_to_background"

    await asyncio.wait_for(manager.subscribe_done().get(), timeout=3)
    task = manager.get(data["task_id"])
    assert task.status is Status.COMPLETED
    assert task.result == "slow result"


async def test_subagent_failure_becomes_error_result(tmp_path, monkeypatch):
    tool, *_rest = _setup(tmp_path, monkeypatch, FailingProvider())
    ctx = _rest[-1]
    with bind_tool_context(ctx):
        result = await tool.execute(_args(subagent_type="demo"))
    assert result.is_error is True
    assert "provider down" in result.content
