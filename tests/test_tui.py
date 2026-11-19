from __future__ import annotations

import asyncio
from pathlib import Path

from mewcode.command import Registry, register_builtins
from mewcode.compact import new_session_context
from mewcode.config import ProviderConfig
from mewcode.llm import Message, StreamEvent
from mewcode.permission import Mode, new_engine
from mewcode.prompt import EXECUTE_DIRECTIVE
from mewcode.session import Writer, list_sessions
from mewcode.tool import new_default_registry
from mewcode.tui.app import MewCodeApp, SessionState
from mewcode.tui.complete import MAX_ROWS, CompletionMenu
from mewcode.tui.resume import select_session

BUILTIN_NAMES = {
    "clear",
    "compact",
    "copy",
    "do",
    "exit",
    "help",
    "hooks",
    "memory",
    "permission",
    "plan",
    "resume",
    "review",
    "session",
    "status",
    "team",
    "worktree",
}


class FakeProvider:
    def __init__(self, text: str = "ok") -> None:
        self._text = text
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.calls += 1
        if self._text:
            yield StreamEvent(text=self._text)
        yield StreamEvent(done=True)


def _app(
    tmp_path, writer=None, ses_ctx=None, mem_mgr=None, skill_loader=None, hook_engine=None
) -> MewCodeApp:
    providers = [
        ProviderConfig(
            name="fake",
            protocol="openai",
            api_key="test-key",
            model="fake-model",
        )
    ]
    return MewCodeApp(
        providers,
        "dev",
        new_default_registry(),
        new_engine(str(tmp_path))[0],
        writer=writer,
        mem_mgr=mem_mgr,
        workspace=str(tmp_path),
        ses_ctx=ses_ctx,
        context_window=128000,
        skill_loader=skill_loader,
        hook_engine=hook_engine,
    )


def _capture(app: MewCodeApp) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []

    def flush() -> None:
        captured.extend(app._pending_println)
        app._pending_println = []

    app.flush_prints = flush  # type: ignore[method-assign]
    return captured


def _text(captured: list[tuple[str, str]]) -> str:
    return "\n".join(msg for _, msg in captured)


async def test_tui_dispatch_help_lists_all_builtins(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        assert await app.dispatch_slash("/help") is True
        text = _text(captured)
        assert len(captured) == 1
        for name in BUILTIN_NAMES:
            assert f"/{name}" in text
        assert app.conv.messages() == []


async def test_tui_unknown_slash_command_friendly(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        assert await app.dispatch_slash("/foobar") is True
        text = _text(captured)
        assert "未知命令" in text
        assert "/help" in text
        assert app.conv.messages() == []


async def test_tui_dispatch_case_insensitive(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        await app.dispatch_slash("/Help")
        assert "/status" in _text(captured)
        assert app.conv.messages() == []


async def test_tui_dispatch_plan_local_only(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        await app.dispatch_slash("/plan")
        assert app.mode() is Mode.PLAN
        assert ("info", "已切换到 PLAN 模式") in captured
        assert app.conv.messages() == []


async def test_tui_dispatch_do_injects_and_sends(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        provider = FakeProvider("done")
        app.provider = provider
        await app.dispatch_slash("/do")

        for _ in range(50):
            if app.idle() and len(app.conv.messages()) >= 2:
                break
            await pilot.pause()

        messages = app.conv.messages()
        assert messages[0].role == "user"
        assert messages[0].content == EXECUTE_DIRECTIVE
        assert messages[-1].role == "assistant"
        assert messages[-1].content == "done"
        assert provider.calls == 1
        assert app.mode() is Mode.DEFAULT


async def test_tui_dispatch_review_injects_and_sends(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.provider = FakeProvider("reviewed")
        await app.dispatch_slash("/review")
        for _ in range(50):
            if app.idle() and len(app.conv.messages()) >= 2:
                break
            await pilot.pause()
        assert "审查" in app.conv.messages()[0].content


async def test_tui_local_command_allowed_while_busy_ui_command_refused(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        app.state = SessionState.STREAMING
        await app.dispatch_slash("/status")
        assert any("Mode:" in msg for _, msg in captured)

        await app.dispatch_slash("/plan")
        assert ("error", "请等待当前任务完成") in captured
        assert app.mode() is Mode.DEFAULT


async def test_tui_handler_exception_is_reported(tmp_path):
    app = _app(tmp_path)

    async def boom(ui) -> None:
        raise RuntimeError("boom")

    async with app.run_test():
        from mewcode.command import Command, Kind

        app.cmd_registry.register(Command("boom", "boom", Kind.LOCAL, boom))
        captured = _capture(app)
        await app.dispatch_slash("/boom")
        assert ("error", "boom") in captured


async def test_tui_completion_filters_and_keys(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("/")
        assert app.completion.active
        assert len(app.completion.items) == 17  # 16 builtins + /skill
        assert {
            "help",
            "status",
            "skill",
        } <= {c.name for c in app.completion.items}

        await pilot.press("s")
        assert [c.name for c in app.completion.items] == ["session", "skill", "status"]
        assert app.query_one("#input").text == "/s"

        await pilot.press("down")
        assert app.completion.cursor == 1
        await pilot.press("up")
        assert app.completion.cursor == 0

        await pilot.press("escape")
        assert not app.completion.active
        assert app.query_one("#input").text == "/s"

        await pilot.press("backspace", "backspace")
        assert not app.completion.active
        assert app.query_one("#input").text == ""


async def test_tui_completion_enter_executes_selected(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        captured = _capture(app)
        await pilot.press("/", "s", "down", "down")
        assert app.completion.selected().name == "status"

        await pilot.press("enter")
        await pilot.pause()

        assert not app.completion.active
        assert app.query_one("#input").text == ""
        assert any("Mode:" in msg for _, msg in captured)


async def test_tui_completion_tab_executes_selected(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        captured = _capture(app)
        await pilot.press("/", "p", "down", "enter")
        await pilot.pause()
        assert app.mode() is Mode.PLAN  # /p 的候选是 permission、plan

        await pilot.press("/", "h", "tab")
        await pilot.pause()
        assert any("/status" in msg for _, msg in captured)


async def test_tui_completion_no_match_enter_goes_unknown(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        captured = _capture(app)
        await pilot.press("/", "z")
        assert app.completion.active
        assert app.completion.items == []

        await pilot.press("enter")
        await pilot.pause()
        assert not app.completion.active
        assert "未知命令" in _text(captured)


async def test_completion_menu_render_limits_rows():
    reg = Registry()
    register_builtins(reg)
    menu = CompletionMenu()
    menu.update("/", reg)

    text = menu.render(80)
    assert text.plain.count("\n") <= MAX_ROWS + 1
    assert "help" in text.plain
    assert "↓ 8 more" in text.plain

    for _ in range(9):
        menu.move_down()
    assert menu.cursor == 9
    assert menu.offset > 0


async def test_tui_compact_degrades_without_compact_fn(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        await app.dispatch_slash("/compact")
        assert "上下文压缩尚未启用" in _text(captured)


async def test_tui_local_queries_use_live_state(tmp_path):
    mem_project = tmp_path / "mem"
    mem_project.mkdir()
    (mem_project / "MEMORY.md").write_text("", encoding="utf-8")
    from mewcode.memory import Manager

    mem_mgr = Manager(str(mem_project), str(tmp_path / "user-mem"))
    ctx = new_session_context(str(tmp_path))
    writer = Writer(ctx.session_dir)
    app = _app(tmp_path, writer=writer, ses_ctx=ctx, mem_mgr=mem_mgr)

    async with app.run_test():
        captured = _capture(app)

        await app.dispatch_slash("/memory")
        assert "project: MEMORY.md" in _text(captured)

        await app.dispatch_slash("/session")
        session_text = _text(captured)
        assert f"Session: {ctx.session_id}" in session_text
        assert f"Path: {writer.path}" in session_text

        await app.dispatch_slash("/status")
        status_text = _text(captured)
        assert "Mode:" in status_text and "default" in status_text
        assert "Tools:" in status_text and "6 enabled" in status_text
        assert "Memories:" in status_text and "1 files" in status_text
        assert str(tmp_path) in status_text


async def test_tui_clear_starts_new_session(tmp_path):
    ctx = new_session_context(str(tmp_path))
    writer = Writer(ctx.session_dir)
    app = _app(tmp_path, writer=writer, ses_ctx=ctx)

    async with app.run_test():
        app._usage_in = 10
        app._usage_out = 5
        app.conv.add_user("hello")
        old_path = writer.path
        captured = _capture(app)

        await app.dispatch_slash("/clear")

        assert app.writer.path != old_path
        assert Path(old_path).is_file()  # 旧会话仍可被 /resume 看到
        listed = {info.id for info in list_sessions(app.sessions_dir)}
        assert ctx.session_id in listed
        assert app.conv.messages() == []
        assert app.usage_in() == 0
        assert app.usage_out() == 0
        assert app.runtime.session is not None
        assert app.session_id() == app.runtime.session.session_id
        assert app.session_id() != ctx.session_id
        assert ("info", "已清空当前会话，开启新会话") in captured


async def test_tui_resume_restores_session(tmp_path):
    ctx = new_session_context(str(tmp_path))
    old_writer = Writer(ctx.session_dir)
    old_writer.model = "fake-model"
    old_writer.append(Message(role="user", content="old question"))
    old_writer.append(Message(role="assistant", content="old answer"))
    old_writer.close()

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await app.dispatch_slash("/resume")
        assert app.state is SessionState.RESUMING
        assert any(item.info.id == ctx.session_id for item in app.resume_items)

        index = next(
            i for i, item in enumerate(app.resume_visible) if item.info.id == ctx.session_id
        )
        select_session(app, index)
        for _ in range(100):
            if app.idle() and app.session_id() == ctx.session_id:
                break
            await pilot.pause()

        assert app.session_id() == ctx.session_id
        assert [m.content for m in app.conv.messages()] == ["old question", "old answer"]
        # 恢复后继续写入同一个 JSONL
        app.conv.add_user("new question")
        app.writer.close()


async def test_tui_dispatch_empty_slash_is_unknown(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        assert await app.dispatch_slash("/") is True
        text = _text(captured)
        assert "未知命令" in text
        assert "未知命令: /" not in text  # 不出现悬空斜杠


async def test_tui_dispatch_non_slash_returns_false(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        assert await app.dispatch_slash("hello") is False
        assert app.conv.messages() == []


def _skill_loader(tmp_path, name="demo", body="Echo hello"):
    from mewcode.skills import SkillLoader

    path = tmp_path / ".mewcode" / "skills" / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A {name} skill\n---\n{body}\n",
        encoding="utf-8",
    )
    loader = SkillLoader(tmp_path, user_dir=tmp_path / "user-skills")
    loader.load_all()
    return loader


async def test_tui_skill_commands_listed(tmp_path):
    app = _app(tmp_path, skill_loader=_skill_loader(tmp_path))
    async with app.run_test():
        captured = _capture(app)
        await app.dispatch_slash("/help")
        text = _text(captured)
        assert "/demo" in text
        assert "[skill]" in text
        assert "/skill" in text

        captured.clear()
        await app.dispatch_slash("/skill list")
        text = _text(captured)
        assert "demo" in text
        assert "[project]" in text


async def test_tui_skill_inline_execution_and_clear(tmp_path):
    app = _app(tmp_path, skill_loader=_skill_loader(tmp_path))
    async with app.run_test() as pilot:
        provider = FakeProvider("done")
        app.provider = provider
        app.skill_executor.set_provider(provider)

        await app.dispatch_slash("/demo")
        for _ in range(100):
            if app.idle() and len(app.conv.messages()) >= 2:
                break
            await pilot.pause()

        assert app.runtime.active_skills.names() == ["demo"]
        assert "Echo hello" in app.conv.messages()[0].content
        assert app.conv.messages()[-1].content == "done"
        assert provider.calls == 1

        captured = _capture(app)
        await app.dispatch_slash("/clear")
        assert app.runtime.active_skills.names() == []
        assert any("已清空" in msg for _, msg in captured)


async def test_tui_skill_reload_registers_hot_skill(tmp_path):
    loader = _skill_loader(tmp_path)
    app = _app(tmp_path, skill_loader=loader)
    path = tmp_path / ".mewcode" / "skills" / "second"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: second\ndescription: Second skill\n---\nbody\n", encoding="utf-8"
    )

    async with app.run_test():
        captured = _capture(app)
        await app.dispatch_slash("/skill reload")
        assert app.cmd_registry.lookup("second") is not None
        assert "新增 1" in _text(captured)

        captured.clear()
        await app.dispatch_slash("/help")
        assert "/second" in _text(captured)


async def test_tui_quit_cancels_background_tasks(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        task = asyncio.create_task(asyncio.sleep(10))
        app._compact_task = task
        await app.dispatch_slash("/exit")
        for _ in range(50):
            if task.cancelled():
                break
            await asyncio.sleep(0)
        assert task.cancelled()
