from __future__ import annotations

import asyncio

import pytest

from mewcode.agent import Agent, MaxTurnsReached
from mewcode.conversation import Conversation
from mewcode.llm import StreamEvent, ToolCall
from mewcode.permission import Mode, Outcome, new_engine
from mewcode.tool import Registry, Result


class ScriptedProvider:
    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = 0
        self.received_reqs = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.calls += 1
        self.received_reqs.append(req)
        idx = min(self.calls - 1, len(self.scripts) - 1)
        for ev in self.scripts[idx]:
            yield ev


class LoopProvider:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.calls += 1
        yield StreamEvent(
            tool_calls=[ToolCall(id=f"c{self.calls}", name=self.tool_name, input="{}")]
        )
        yield StreamEvent(done=True)


class FakeReadTool:
    read_only = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "read"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, args: str) -> Result:
        return Result(content="file content")


class FakeWriteTool:
    read_only = False

    def __init__(self) -> None:
        self.exec_count = 0

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "write"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, args: str) -> Result:
        self.exec_count += 1
        return Result(content="written")


def _engine(tmp_path):
    return new_engine(str(tmp_path))[0]


def _registry(tool) -> Registry:
    registry = Registry()
    registry.register(tool)
    return registry


async def test_returns_final_text(tmp_path):
    provider = ScriptedProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    conv = Conversation()
    agent = Agent(provider, _registry(FakeReadTool()), "dev", _engine(tmp_path))
    text = await agent.run_to_completion(conv, "task")
    assert text == "ok"
    assert conv.messages()[0].content == "task"


async def test_runs_tools_then_returns_text(tmp_path):
    provider = ScriptedProvider(
        [
            [
                StreamEvent(tool_calls=[ToolCall(id="c1", name="read_file", input="{}")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="final"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    agent = Agent(provider, _registry(FakeReadTool()), "dev", _engine(tmp_path))
    text = await agent.run_to_completion(conv, "task")
    assert text == "final"
    assert provider.calls == 2
    assert any(message.role == "tool" for message in conv.messages())


async def test_max_turns_reached(tmp_path):
    conv = Conversation()
    agent = Agent(
        LoopProvider("read_file"),
        _registry(FakeReadTool()),
        "dev",
        _engine(tmp_path),
        max_turns=2,
    )
    with pytest.raises(MaxTurnsReached):
        await agent.run_to_completion(conv, "loop")


async def test_dont_ask_allows_ask_tool(tmp_path):
    write = FakeWriteTool()
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            input='{"path": "a.txt", "content": "x"}',
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(
        provider,
        _registry(write),
        "dev",
        _engine(tmp_path),
        dont_ask=True,
    )
    text = await agent.run_to_completion(Conversation(), "write")
    assert text == "done"
    assert write.exec_count == 1


async def test_approval_upgrader_is_used(tmp_path):
    write = FakeWriteTool()
    calls = []

    async def upgrader(req):
        calls.append(req)
        return Outcome.ALLOW_ONCE, True

    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            input='{"path": "a.txt", "content": "x"}',
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(
        provider,
        _registry(write),
        "dev",
        _engine(tmp_path),
        approval_upgrader=upgrader,
    )
    text = await agent.run_to_completion(Conversation(), "write")
    assert text == "done"
    assert write.exec_count == 1
    assert calls and calls[0].name == "write_file"
    assert "[来自 SubAgent]" in calls[0].reason


async def test_events_are_forwarded(tmp_path):
    provider = ScriptedProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    events: asyncio.Queue = asyncio.Queue()
    agent = Agent(provider, _registry(FakeReadTool()), "dev", _engine(tmp_path))
    await agent.run_to_completion(Conversation(), "task", events)
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    assert any(event.text == "ok" for event in seen)
    assert any(event.done for event in seen)


async def test_system_prompt_override(tmp_path):
    provider = ScriptedProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    agent = Agent(
        provider,
        _registry(FakeReadTool()),
        "dev",
        _engine(tmp_path),
        system_prompt="CUSTOM SYSTEM",
    )
    await agent.run_to_completion(Conversation(), "task")
    assert provider.received_reqs[0].system.stable == "CUSTOM SYSTEM"


async def test_permission_mode_overrides_run_mode(tmp_path):
    provider = ScriptedProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    agent = Agent(
        provider,
        _registry(FakeReadTool()),
        "dev",
        _engine(tmp_path),
        permission_mode=Mode.BYPASS,
    )
    assert agent._permission_mode is Mode.BYPASS


async def test_tool_not_in_allowed_list_is_blocked(tmp_path):
    write = FakeWriteTool()
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            input='{"path": "a.txt", "content": "x"}',
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(
        provider,
        _registry(write),
        "dev",
        _engine(tmp_path),
        allowed_tools=["read_file"],
    )
    await agent.run_to_completion(Conversation(), "write")
    assert write.exec_count == 0
