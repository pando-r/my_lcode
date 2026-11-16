from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mewcode.agent import Agent, Phase
from mewcode.agent.runtime import SessionRuntime
from mewcode.conversation import Conversation
from mewcode.hook import load
from mewcode.hook.executor import _find_sh
from mewcode.llm import StreamEvent, ToolCall
from mewcode.permission import Mode, Outcome, new_engine
from mewcode.tool import Registry, Result

needs_sh = pytest.mark.skipif(_find_sh() is None, reason="no POSIX shell available")


class FakeReadTool:
    read_only: bool = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "read"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

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


def _hooks(tmp_path, monkeypatch, body: str):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    path = tmp_path / ".mewcode" / "hooks.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return load(tmp_path)


def _registry() -> Registry:
    registry = Registry()
    registry.register(FakeReadTool())
    return registry


def _tool_call_script(name: str, call_id: str = "c1", args: str = '{"path": "x"}'):
    return [
        StreamEvent(tool_calls=[ToolCall(id=call_id, name=name, input=args)]),
        StreamEvent(done=True),
    ]


def _final_script(text: str = "done"):
    return [StreamEvent(text=text), StreamEvent(done=True)]


@needs_sh
async def test_pretooluse_shell_exit_2_blocks_tool(tmp_path, monkeypatch):
    hook_engine = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: guard
    event: PreToolUse
    action:
      type: shell
      command: "printf 'blocked by policy' >&2; exit 2"
""",
    )
    provider = ScriptedProvider(
        [_tool_call_script("write_file", args='{"path": "a.txt", "content": "x"}'), _final_script()]
    )
    write = FakeWriteTool()
    registry = Registry()
    registry.register(write)
    conv = Conversation()
    conv.add_user("write a file")

    events = [
        ev
        async for ev in Agent(
            provider,
            registry,
            "dev",
            new_engine(str(tmp_path))[0],
            hook_engine=hook_engine,
        ).run(conv, Mode.BYPASS, asyncio.Event())
    ]

    assert write.exec_count == 0
    assert provider.calls == 2
    tool_msgs = [m for m in conv.messages() if m.role == "tool"]
    assert tool_msgs[-1].tool_results[0].content == "[hook guard] blocked by policy"
    assert tool_msgs[-1].tool_results[0].is_error is True
    end_events = [ev.tool for ev in events if ev.tool is not None and ev.tool.phase is Phase.END]
    assert end_events[-1].is_error is True


@needs_sh
async def test_shell_hook_receives_stable_payload(tmp_path, monkeypatch):
    target = (tmp_path / "payload.json").as_posix()
    hook_engine = _hooks(
        tmp_path,
        monkeypatch,
        f"""
hooks:
  - name: capture
    event: PreToolUse
    action:
      type: shell
      command: "cat > '{target}'"
""",
    )
    provider = ScriptedProvider([_tool_call_script("read_file"), _final_script()])
    conv = Conversation()
    conv.add_user("read")

    async for _ in Agent(
        provider,
        _registry(),
        "dev",
        new_engine(str(tmp_path))[0],
        hook_engine=hook_engine,
    ).run(conv, Mode.BYPASS, asyncio.Event()):
        pass

    payload = json.loads((tmp_path / "payload.json").read_text(encoding="utf-8"))
    assert payload["event"] == "PreToolUse"
    assert payload["tool_name"] == "read_file"
    assert payload["tool_input"] == {"path": "x"}
    assert payload["mode"] == "bypassPermissions"
    assert list(payload) == sorted(payload)


async def test_post_tool_use_prompt_reaches_next_request(tmp_path, monkeypatch):
    hook_engine = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: post
    event: PostToolUse
    action: {type: prompt, text: POST-NOTE}
""",
    )
    provider = ScriptedProvider([_tool_call_script("read_file"), _final_script()])
    conv = Conversation()
    conv.add_user("read")

    async for _ in Agent(
        provider,
        _registry(),
        "dev",
        new_engine(str(tmp_path))[0],
        hook_engine=hook_engine,
    ).run(conv, Mode.BYPASS, asyncio.Event()):
        pass

    assert "POST-NOTE" not in provider.received_reqs[0].reminder
    assert "POST-NOTE" in provider.received_reqs[1].reminder


async def test_pre_user_message_prompt_injected(tmp_path, monkeypatch):
    hook_engine = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: pre
    event: PreUserMessage
    action: {type: prompt, text: PRE-NOTE}
""",
    )
    provider = ScriptedProvider([_final_script()])
    conv = Conversation()
    conv.add_user("hello")

    async for _ in Agent(
        provider,
        _registry(),
        "dev",
        new_engine(str(tmp_path))[0],
        hook_engine=hook_engine,
    ).run(conv, Mode.BYPASS, asyncio.Event()):
        pass

    assert "PRE-NOTE" in provider.received_reqs[0].reminder
    # reminder 不写入对话历史
    assert all("PRE-NOTE" not in msg.content for msg in conv.messages())


async def test_stop_prompt_stays_out_of_history(tmp_path, monkeypatch):
    hook_engine = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: stop
    event: Stop
    action: {type: prompt, text: STOP-NOTE}
""",
    )
    provider = ScriptedProvider([_final_script("answer")])
    conv = Conversation()
    conv.add_user("hello")
    runtime = SessionRuntime()

    async for _ in Agent(
        provider,
        _registry(),
        "dev",
        new_engine(str(tmp_path))[0],
        runtime=runtime,
        hook_engine=hook_engine,
    ).run(conv, Mode.BYPASS, asyncio.Event()):
        pass

    assert runtime.take_reminders() == ["STOP-NOTE"]
    assert conv.messages()[-1].content == "answer"


async def test_pre_user_message_only_once(tmp_path, monkeypatch):
    hook_engine = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: once
    event: PreUserMessage
    only_once: true
    action: {type: prompt, text: ONCE-NOTE}
""",
    )
    provider = ScriptedProvider([_tool_call_script("read_file"), _final_script()])
    conv = Conversation()
    conv.add_user("read")

    async for _ in Agent(
        provider,
        _registry(),
        "dev",
        new_engine(str(tmp_path))[0],
        hook_engine=hook_engine,
    ).run(conv, Mode.BYPASS, asyncio.Event()):
        pass

    assert "ONCE-NOTE" in provider.received_reqs[0].reminder
    assert "ONCE-NOTE" not in provider.received_reqs[1].reminder


async def test_notification_fires_before_approval(tmp_path, monkeypatch):
    hook_engine = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: notify
    event: Notification
    if:
      all_of:
        - field: kind
          match: {type: exact, value: approval}
    action: {type: prompt, text: APPROVAL-NOTE}
""",
    )
    provider = ScriptedProvider(
        [
            _tool_call_script("write_file", args='{"path": "a.txt", "content": "x"}'),
            _final_script(),
        ]
    )
    write = FakeWriteTool()
    registry = Registry()
    registry.register(write)
    conv = Conversation()
    conv.add_user("write")
    runtime = SessionRuntime()

    async for ev in Agent(
        provider,
        registry,
        "dev",
        new_engine(str(tmp_path))[0],
        runtime=runtime,
        hook_engine=hook_engine,
    ).run(conv, Mode.DEFAULT, asyncio.Event()):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.ALLOW_ONCE)

    # reminder 在下一轮 LLM 请求前被消费，不会停留在队列里。
    assert "APPROVAL-NOTE" in provider.received_reqs[1].reminder
    assert write.exec_count == 1
