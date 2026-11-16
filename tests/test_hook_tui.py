from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import RichLog

from mewcode.compact import new_session_context
from mewcode.config import ProviderConfig
from mewcode.hook import load
from mewcode.hook.executor import _find_sh
from mewcode.llm import Message, StreamEvent
from mewcode.permission import new_engine
from mewcode.session import Writer
from mewcode.tool import new_default_registry
from mewcode.tui.app import MewCodeApp
from mewcode.tui.resume import select_session
from mewcode.tui.stream import ChatInput

needs_sh = pytest.mark.skipif(_find_sh() is None, reason="no POSIX shell available")


class RecordingProvider:
    def __init__(self, text: str = "ok") -> None:
        self._text = text
        self.received: list = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.received.append(req)
        if self._text:
            yield StreamEvent(text=self._text)
        yield StreamEvent(done=True)


def _isolate_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _hooks(tmp_path, monkeypatch, body: str):
    _isolate_home(tmp_path, monkeypatch)
    path = tmp_path / ".mewcode" / "hooks.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return load(tmp_path), path


def _app(tmp_path, hook_engine=None) -> MewCodeApp:
    providers = [
        ProviderConfig(name="fake", protocol="openai", api_key="test-key", model="fake-model")
    ]
    return MewCodeApp(
        providers,
        "dev",
        new_default_registry(),
        new_engine(str(tmp_path))[0],
        workspace=str(tmp_path),
        context_window=128000,
        hook_engine=hook_engine,
    )


def _capture(app: MewCodeApp) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []

    def flush() -> None:
        captured.extend(app._pending_println)
        app._pending_println = []

    app.flush_prints = flush  # type: ignore[method-assign]
    return captured


def _log_text(app: MewCodeApp) -> str:
    log = app.query_one("#log", RichLog)
    return "\n".join(line.text for line in log.lines)


async def _wait_idle(app, pilot, turns: int = 1) -> None:
    for _ in range(200):
        if app.idle() and app.runtime.turn_count >= turns:
            return
        await pilot.pause()


async def test_hooks_command_lists_grouped_rules(tmp_path, monkeypatch):
    hook_engine, path = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: guard
    event: PreToolUse
    only_once: true
    action: {type: prompt, text: x}
  - name: telemetry
    event: PostToolUse
    async: true
    action: {type: prompt, text: y}
  - name: notify
    event: Stop
    action: {type: shell, command: "true"}
""",
    )
    app = _app(tmp_path, hook_engine=hook_engine)
    async with app.run_test():
        captured = _capture(app)
        await app.dispatch_slash("/hooks")
        text = "\n".join(msg for _, msg in captured)
        assert "Hooks:" in text
        assert "PreToolUse:" in text
        assert "- guard [once] (prompt)" in text
        assert "- telemetry [async] (prompt)" in text
        assert "- notify (shell)" in text
        assert "Loaded from:" in text
        assert str(path) in text


@needs_sh
async def test_user_prompt_submit_block_keeps_input_and_history(tmp_path, monkeypatch):
    hook_engine, _ = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: gate
    event: UserPromptSubmit
    action:
      type: shell
      command: "printf 'not now' >&2; exit 2"
""",
    )
    app = _app(tmp_path, hook_engine=hook_engine)
    async with app.run_test():
        app.query_one("#input", ChatInput).load_text("hello")
        await app.submit("hello")

        assert app.conv.messages() == []
        assert app.query_one("#input", ChatInput).text == "hello"
        assert "[hook gate] not now" in _log_text(app)


async def test_session_start_prompt_reaches_first_request(tmp_path, monkeypatch):
    hook_engine, _ = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: boot
    event: SessionStart
    action: {type: prompt, text: BOOT-NOTE}
""",
    )
    app = _app(tmp_path, hook_engine=hook_engine)
    async with app.run_test() as pilot:
        provider = RecordingProvider("ok")
        app.provider = provider
        await app.submit("hi")
        await _wait_idle(app, pilot)

        assert "BOOT-NOTE" in provider.received[0].reminder


@needs_sh
async def test_clear_dispatches_session_end_and_start_and_resets_once(tmp_path, monkeypatch):
    end_file = (tmp_path / "end.json").as_posix()
    hook_engine, _ = _hooks(
        tmp_path,
        monkeypatch,
        f"""
hooks:
  - name: boot
    event: SessionStart
    only_once: true
    action: {{type: prompt, text: START-NOTE}}
  - name: bye
    event: SessionEnd
    action:
      type: shell
      command: "cat > '{end_file}'"
""",
    )
    app = _app(tmp_path, hook_engine=hook_engine)
    async with app.run_test():
        assert app.runtime.take_reminders() == ["START-NOTE"]
        await app.dispatch_slash("/clear")

        # only_once 状态被 reset，新会话再次收到 SessionStart。
        assert app.runtime.take_reminders() == ["START-NOTE"]
        payload = json.loads((tmp_path / "end.json").read_text(encoding="utf-8"))
        assert payload["event"] == "SessionEnd"
        assert payload["reason"] == "clear"


async def test_compact_dispatches_pre_and_post(tmp_path, monkeypatch):
    hook_engine, _ = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: pre
    event: PreCompact
    action: {type: prompt, text: PRE-NOTE}
  - name: post
    event: PostCompact
    action: {type: prompt, text: POST-NOTE}
""",
    )

    async def fake_compact(msgs):
        return msgs

    app = _app(tmp_path, hook_engine=hook_engine)
    async with app.run_test():
        app.compact_fn = fake_compact
        await app._run_force_compact()
        assert app.runtime.take_reminders() == ["PRE-NOTE", "POST-NOTE"]


async def test_ensure_session_end_fires_once(tmp_path, monkeypatch):
    hook_engine, _ = _hooks(
        tmp_path,
        monkeypatch,
        """
hooks:
  - name: bye
    event: SessionEnd
    action: {type: prompt, text: END-NOTE}
""",
    )
    app = _app(tmp_path, hook_engine=hook_engine)
    async with app.run_test():
        await app.ensure_session_end("exit")
        await app.ensure_session_end("exit")
        assert app.runtime.take_reminders() == ["END-NOTE"]


@needs_sh
async def test_resume_dispatches_session_resume(tmp_path, monkeypatch):
    ctx = new_session_context(str(tmp_path))
    old_writer = Writer(ctx.session_dir)
    old_writer.append(Message(role="user", content="old question"))
    old_writer.append(Message(role="assistant", content="old answer"))
    old_writer.close()

    end_file = (tmp_path / "end.json").as_posix()
    hook_engine, _ = _hooks(
        tmp_path,
        monkeypatch,
        f"""
hooks:
  - name: bye
    event: SessionEnd
    action:
      type: shell
      command: "cat > '{end_file}'"
  - name: back
    event: SessionResume
    action: {{type: prompt, text: RESUME-NOTE}}
""",
    )
    app = _app(tmp_path, hook_engine=hook_engine)
    async with app.run_test() as pilot:
        await app.dispatch_slash("/resume")
        index = next(
            i for i, item in enumerate(app.resume_visible) if item.info.id == ctx.session_id
        )
        select_session(app, index)
        for _ in range(100):
            if app.idle() and app.session_id() == ctx.session_id:
                break
            await pilot.pause()

        assert app.runtime.take_reminders() == ["RESUME-NOTE"]
        payload = json.loads((tmp_path / "end.json").read_text(encoding="utf-8"))
        assert payload["reason"] == "resume"
