from __future__ import annotations

import asyncio
from pathlib import Path

from lcode.agent import (
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    Phase,
)
from lcode.agent.runtime import SessionRuntime
from lcode.conversation import Conversation
from lcode.llm import StreamEvent, ToolCall
from lcode.permission import Mode, Outcome, new_engine
from lcode.tool import Registry, Result


def _engine():
    return new_engine(str(Path.cwd().resolve()))[0]


class FakeReadTool:
    read_only: bool = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "read"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    async def execute(self, args: str) -> Result:
        return Result(content="file content")


class FakeWriteTool:
    read_only: bool = False

    def __init__(self) -> None:
        self.exec_count = 0

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "write"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        }

    async def execute(self, args: str) -> Result:
        self.exec_count += 1
        return Result(content="written")


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
    def __init__(self, tool_name: str):
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


class InstrumentedReadTool:
    read_only: bool = True

    def __init__(self) -> None:
        self.concurrent = 0
        self.peak = 0

    def name(self) -> str:
        return "read"

    def description(self) -> str:
        return "read"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        await asyncio.sleep(0.05)
        self.concurrent -= 1
        return Result(content=f"read:{args}")


class InstrumentedWriteTool:
    read_only: bool = False

    def name(self) -> str:
        return "write"

    def description(self) -> str:
        return "write"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        return Result(content=f"write:{args}")


class SlowTool:
    read_only: bool = False

    def name(self) -> str:
        return "slow"

    def description(self) -> str:
        return "slow"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(0.2)
        return Result(content="done")


async def test_multi_round():
    provider = ScriptedProvider(
        [
            [
                StreamEvent(text="reading"),
                StreamEvent(
                    tool_calls=[ToolCall(id="c1", name="read_file", input='{"path": "x"}')]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done reading"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(FakeReadTool())
    conv = Conversation()
    conv.add_user("read and summarize")

    events = [
        ev
        async for ev in Agent(provider, registry, "dev", _engine()).run(
            conv, Mode.BYPASS, asyncio.Event()
        )
    ]

    assert any(ev.iter == 1 for ev in events)
    assert events[-1].done
    assert conv.messages()[-1].content == "done reading"


async def test_iteration_limit():
    provider = LoopProvider("read_file")
    registry = Registry()
    registry.register(FakeReadTool())
    conv = Conversation()
    conv.add_user("keep going")

    events = [
        ev
        async for ev in Agent(provider, registry, "dev", _engine()).run(
            conv, Mode.BYPASS, asyncio.Event()
        )
    ]

    assert provider.calls == MAX_ITERATIONS
    assert any(ev.notice == NOTICE_MAX_ITER for ev in events)


async def test_unknown_tools_stop():
    provider = LoopProvider("ghost")
    registry = Registry()
    registry.register(FakeReadTool())
    conv = Conversation()
    conv.add_user("use a ghost tool")

    events = [
        ev
        async for ev in Agent(provider, registry, "dev", _engine()).run(
            conv, Mode.BYPASS, asyncio.Event()
        )
    ]

    assert provider.calls == MAX_UNKNOWN_RUN
    assert any(ev.notice == NOTICE_UNKNOWN_TOOLS for ev in events)


async def test_concurrent_batch():
    read = InstrumentedReadTool()
    write = InstrumentedWriteTool()
    registry = Registry()
    registry.register(read)
    registry.register(write)

    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(id="r1", name="read", input="{}"),
                        ToolCall(id="r2", name="read", input="{}"),
                        ToolCall(id="w1", name="write", input="{}"),
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="finished"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("go")

    async for _ in Agent(provider, registry, "dev", _engine()).run(
        conv, Mode.BYPASS, asyncio.Event()
    ):
        pass

    assert read.peak >= 2


async def test_cancel_consistent():
    registry = Registry()
    registry.register(SlowTool())
    provider = ScriptedProvider(
        [
            [
                StreamEvent(tool_calls=[ToolCall(id="c1", name="slow", input="{}")]),
                StreamEvent(done=True),
            ]
        ]
    )
    conv = Conversation()
    conv.add_user("slow")

    cancel = asyncio.Event()
    async for ev in Agent(provider, registry, "dev", _engine()).run(conv, Mode.BYPASS, cancel):
        if ev.tool and ev.tool.phase is Phase.START:
            cancel.set()

    assert conv.last_role() == "assistant"
    assert conv.messages()[-1].content == NOTICE_CANCELLED


async def test_deny_sandbox(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            input='{"path": "/etc/passwd", "content": "x"}',
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("write outside")

    async for _ in Agent(provider, registry, "dev", new_engine(str(tmp_path))[0]).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        pass

    assert tool.exec_count == 0
    tool_msgs = [m for m in conv.messages() if m.role == "tool"]
    assert tool_msgs[0].tool_results[0].is_error


async def test_ask_allow_once(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1", name="write_file", input='{"path": "a.txt", "content": "x"}'
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("write")

    async for ev in Agent(provider, registry, "dev", new_engine(str(tmp_path))[0]).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.ALLOW_ONCE)

    assert tool.exec_count == 1


async def test_ask_permanent(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1", name="write_file", input='{"path": "a.txt", "content": "x"}'
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("write")

    async for ev in Agent(provider, registry, "dev", new_engine(str(tmp_path))[0]).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.ALLOW_FOREVER)

    assert tool.exec_count == 1
    local = tmp_path / ".lcode" / "settings.local.yaml"
    assert local.exists()
    assert "Write(" in local.read_text(encoding="utf-8")


async def test_ask_deny_once(tmp_path):
    tool = FakeWriteTool()
    registry = Registry()
    registry.register(tool)
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1", name="write_file", input='{"path": "a.txt", "content": "x"}'
                        )
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("write")

    async for ev in Agent(provider, registry, "dev", new_engine(str(tmp_path))[0]).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.DENY_ONCE)

    assert tool.exec_count == 0
    tool_msgs = [m for m in conv.messages() if m.role == "tool"]
    assert tool_msgs[0].tool_results[0].is_error


class FakeMemoryManager:
    def __init__(self) -> None:
        self.calls: list[list] = []

    async def update_async(self, msgs) -> None:
        self.calls.append(list(msgs))


async def test_memory_trigger_survives_agent_rebuild():
    # Agent 每轮都会重建，但 turn_count 在 SessionRuntime 上，第 5 轮仍应触发。
    manager = FakeMemoryManager()
    runtime = SessionRuntime(memory_manager=manager)
    provider = ScriptedProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    conv = Conversation()

    for i in range(5):
        conv.add_user(f"第 {i + 1} 轮")
        agent = Agent(provider, Registry(), "dev", _engine(), runtime=runtime)
        async for _ in agent.run(conv, Mode.DEFAULT, asyncio.Event()):
            pass

    await asyncio.sleep(0)
    assert agent.turn_count == 5
    assert len(manager.calls) == 1


async def test_memory_keyword_triggers_on_first_turn():
    manager = FakeMemoryManager()
    runtime = SessionRuntime(memory_manager=manager)
    provider = ScriptedProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    conv = Conversation()
    conv.add_user("记住：这个项目用 uv 管理依赖")
    agent = Agent(provider, Registry(), "dev", _engine(), runtime=runtime)

    async for _ in agent.run(conv, Mode.DEFAULT, asyncio.Event()):
        pass

    await asyncio.sleep(0)
    assert len(manager.calls) == 1
