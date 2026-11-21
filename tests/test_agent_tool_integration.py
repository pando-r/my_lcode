from __future__ import annotations

import asyncio
import json
from pathlib import Path

from lcode.agent import Agent
from lcode.agent.agent_tool import AgentTool
from lcode.conversation import Conversation
from lcode.hook import load as load_hooks
from lcode.llm import StreamEvent, ToolCall
from lcode.permission import Mode, Outcome, new_engine
from lcode.subagent import load_catalog
from lcode.task import Manager, Status
from lcode.tool import Registry, Result


class ScriptedProvider:
    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = 0
        self.received = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.calls += 1
        self.received.append(req)
        idx = min(self.calls - 1, len(self.scripts) - 1)
        for event in self.scripts[idx]:
            yield event


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


def _isolate(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)


def _write_agent(tmp_path, name="demo") -> None:
    path = tmp_path / ".lcode" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: demo\nmaxTurns: 5\n---\nSUB SYSTEM",
        encoding="utf-8",
    )


def _agent_call(args: dict) -> list[StreamEvent]:
    return [
        StreamEvent(tool_calls=[ToolCall(id="a1", name="Agent", input=json.dumps(args))]),
        StreamEvent(done=True),
    ]


async def _run_main(tmp_path, monkeypatch, provider, registry, manager, tool):
    catalog = load_catalog(tmp_path)
    tool._catalog = catalog
    registry.register(tool)
    parent = Agent(provider, registry, "dev", new_engine(str(tmp_path))[0])
    conv = Conversation()
    conv.add_user("use a subagent")
    events = [ev async for ev in parent.run(conv, Mode.BYPASS, asyncio.Event())]
    return conv, events


async def test_main_agent_calls_definition_subagent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write_agent(tmp_path)
    provider = ScriptedProvider(
        [
            _agent_call({"prompt": "work", "description": "d", "subagent_type": "demo"}),
            [StreamEvent(text="sub done"), StreamEvent(done=True)],
            [StreamEvent(text="main done"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(FakeReadTool())
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager, bg_enabled=True)

    conv, events = await _run_main(tmp_path, monkeypatch, provider, registry, manager, tool)

    tool_results = [m for m in conv.messages() if m.role == "tool"]
    assert tool_results[0].tool_results[0].content == "sub done"
    assert conv.messages()[-1].content == "main done"
    assert any(event.done for event in events)
    # 子 Agent 的工具列表不含 Agent
    sub_tool_names = [definition.name for definition in provider.received[1].tools]
    assert "Agent" not in sub_tool_names


async def test_main_agent_background_subagent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write_agent(tmp_path)
    provider = ScriptedProvider(
        [
            _agent_call(
                {
                    "prompt": "work",
                    "description": "d",
                    "subagent_type": "demo",
                    "run_in_background": True,
                    "name": "worker",
                }
            ),
            [StreamEvent(text="bg done"), StreamEvent(done=True)],
            # 后台子 Agent 与主对话并发消费 provider：后续脚本保持一致避免竞态。
            [StreamEvent(text="bg done"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(FakeReadTool())
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager, bg_enabled=True)

    conv, _ = await _run_main(tmp_path, monkeypatch, provider, registry, manager, tool)
    tool_results = [m for m in conv.messages() if m.role == "tool"]
    payload = json.loads(tool_results[0].tool_results[0].content)
    assert payload["status"] == "async_launched"

    await asyncio.wait_for(manager.subscribe_done().get(), timeout=2)
    task = manager.get(payload["task_id"])
    assert task.status is Status.COMPLETED
    assert task.result == "bg done"
    assert task.name == "worker"


async def test_fork_subagent_nested_agent_call_is_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    provider = ScriptedProvider(
        [
            _agent_call(
                {
                    "prompt": "nested?",
                    "description": "nested",
                    "subagent_type": "demo",
                    "run_in_background": True,
                }
            ),
            [StreamEvent(text="fork final"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(FakeReadTool())
    manager = Manager()

    async def allow(req):
        return Outcome.ALLOW_ONCE

    manager.set_approval_handler(allow)
    tool = AgentTool(load_catalog(tmp_path), manager, bg_enabled=True)
    registry.register(tool)
    parent = Agent(provider, registry, "dev", new_engine(str(tmp_path))[0])
    conv = Conversation()
    conv.add_user("parent")
    from lcode.agent.tool_context import ToolCallContext, bind_tool_context

    ctx = ToolCallContext(parent, conv)
    with bind_tool_context(ctx):
        result = await tool.execute(json.dumps({"prompt": "fork task", "description": "d"}))
    payload = json.loads(result.content)
    await asyncio.wait_for(manager.subscribe_done().get(), timeout=2)
    task = manager.get(payload["task_id"])
    tool_messages = [m for m in task.conv.messages() if m.role == "tool"]
    contents = [r.content for m in tool_messages for r in m.tool_results]
    assert any("Fork 子 Agent 不能再启动 Agent" in content for content in contents)
    assert task.result == "fork final"


async def test_hooks_apply_inside_subagent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write_agent(tmp_path)
    hooks_path = tmp_path / ".lcode" / "hooks.yaml"
    hooks_path.write_text(
        """
hooks:
  - name: sub-note
    event: PreToolUse
    if:
      all_of:
        - field: tool_name
          match: {type: exact, value: read_file}
    action: {type: prompt, text: SUB-HOOK-NOTE}
""",
        encoding="utf-8",
    )
    provider = ScriptedProvider(
        [
            _agent_call({"prompt": "read", "description": "d", "subagent_type": "demo"}),
            [
                StreamEvent(
                    tool_calls=[ToolCall(id="r1", name="read_file", input='{"path": "x"}')]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="sub final"), StreamEvent(done=True)],
            [StreamEvent(text="main done"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(FakeReadTool())
    manager = Manager()
    hook_engine = load_hooks(tmp_path)
    tool = AgentTool(load_catalog(tmp_path), manager, bg_enabled=True)

    catalog = load_catalog(tmp_path)
    tool._catalog = catalog
    registry.register(tool)
    parent = Agent(
        provider,
        registry,
        "dev",
        new_engine(str(tmp_path))[0],
        hook_engine=hook_engine,
    )
    conv = Conversation()
    conv.add_user("use a subagent")
    async for _ in parent.run(conv, Mode.BYPASS, asyncio.Event()):
        pass

    # 子 Agent 第二次 LLM 请求（read_file 之后）应收到 hook 注入的 reminder。
    assert "SUB-HOOK-NOTE" in provider.received[2].reminder
