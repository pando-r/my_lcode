from __future__ import annotations

import asyncio
from pathlib import Path

from lcode.command import Registry, register_builtins
from lcode.compact import new_session_context
from lcode.config import ProviderConfig
from lcode.llm import Message, StreamEvent
from lcode.permission import Mode, new_engine
from lcode.prompt import EXECUTE_DIRECTIVE
from lcode.session import Writer, list_sessions
from lcode.tool import new_default_registry
from lcode.tui.app import LcodeApp, SessionState
from lcode.tui.complete import MAX_ROWS, CompletionMenu
from lcode.tui.resume import select_session

BUILTIN_NAMES = {
    "audio",
    "clear",
    "compact",
    "copy",
    "do",
    "exit",
    "help",
    "hooks",
    "image",
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
) -> LcodeApp:
    providers = [
        ProviderConfig(
            name="fake",
            protocol="openai",
            api_key="test-key",
            model="fake-model",
        )
    ]
    return LcodeApp(
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


def _capture(app: LcodeApp) -> list[tuple[str, str]]:
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
        from lcode.command import Command, Kind

        app.cmd_registry.register(Command("boom", "boom", Kind.LOCAL, boom))
        captured = _capture(app)
        await app.dispatch_slash("/boom")
        assert ("error", "boom") in captured


async def test_tui_completion_filters_and_keys(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("/")
        assert app.completion.active
        assert len(app.completion.items) == 19  # 18 builtins + /skill
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
    assert "↓ 10 more" in text.plain

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
    from lcode.memory import Manager

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
        # 工具数量随默认注册表演进（notify 加入后是 7，send_mail 加入后是 8），
        # 断言用真实计数而不是硬编码。
        assert "Tools:" in status_text and f"{app.tool_count()} enabled" in status_text
        assert app.tool_count() == 8
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
    from lcode.skills import SkillLoader

    path = tmp_path / ".lcode" / "skills" / name
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
    path = tmp_path / ".lcode" / "skills" / "second"
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


async def test_tui_image_command_injects_vision_answer(tmp_path):
    from lcode.config import VisionConfig
    from lcode.tool.view_image import ViewImageTool

    img = tmp_path / "shot.png"
    img.write_bytes(_png(8, 8))

    async def fake_asker(cfg, payloads, question, detail):
        return "报错在 term.py 第 3 行"

    app = _app(tmp_path)
    app._tool_registry.register(
        ViewImageTool(VisionConfig(api_key="k", model="qwen-vl-max"), asker=fake_asker)
    )
    async with app.run_test() as pilot:
        app.provider = FakeProvider("我看过了")
        captured = _capture(app)
        app.view_image(str(img), "报错在哪？")
        for _ in range(100):
            if app.idle() and len(app.conv.messages()) >= 2:
                break
            await pilot.pause()

        messages = app.conv.messages()
        assert messages[0].role == "user"
        assert "报错在 term.py 第 3 行" in messages[0].content
        assert str(img) in messages[0].content
        # 历史里只有文字：图片本体不进对话，JSONL 不会被 base64 撑大
        assert "base64" not in messages[0].content
        assert messages[-1].content == "我看过了"
        assert any("正在看图" in msg for _, msg in captured)


async def test_tui_image_command_without_vision_shows_hint(tmp_path):
    from lcode.tool.view_image import VISION_SETUP_HINT

    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        app.view_image(str(tmp_path / "x.png"), "看图")
        for _ in range(50):
            if captured:
                break
            await asyncio.sleep(0)
        assert VISION_SETUP_HINT in _text(captured)


async def test_tui_audio_command_injects_transcript(tmp_path):
    from lcode.audio import Segment, Transcript
    from lcode.config import AudioConfig
    from lcode.tool.transcribe_audio import TranscribeAudioTool

    audio = tmp_path / "note.wav"
    audio.write_bytes(_wav())

    async def fake_transcriber(cfg, path, language="", prompt=""):
        return Transcript(
            [Segment(0, 3, "先做媒体层")], language="zh", model="whisper-1"
        )

    app = _app(tmp_path)
    app._tool_registry.register(
        TranscribeAudioTool(AudioConfig(api_key="k"), transcriber=fake_transcriber)
    )
    async with app.run_test() as pilot:
        app.provider = FakeProvider("听完了")
        captured = _capture(app)
        app.transcribe_audio(str(audio), "关于重构")
        for _ in range(100):
            if app.idle() and len(app.conv.messages()) >= 2:
                break
            await pilot.pause()

        messages = app.conv.messages()
        assert "先做媒体层" in messages[0].content
        assert "[00:00 → 00:03]" in messages[0].content
        assert "关于重构" in messages[0].content
        assert messages[-1].content == "听完了"
        assert any("正在转写" in msg for _, msg in captured)


async def test_tui_audio_command_without_config_shows_hint(tmp_path):
    from lcode.tool.transcribe_audio import AUDIO_SETUP_HINT

    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        app.transcribe_audio(str(tmp_path / "x.wav"), "")
        for _ in range(50):
            if captured:
                break
            await asyncio.sleep(0)
        assert AUDIO_SETUP_HINT in _text(captured)


def _png(width: int = 8, height: int = 8) -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x90\x90\x90" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def _wav() -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


async def test_tui_attach_button_queues_files(tmp_path, monkeypatch):
    from lcode.tui import attach as attach_mod

    img = tmp_path / "shot.png"
    img.write_bytes(_png())

    async def fake_pick(title: str = "") -> list[str]:
        return [str(img)]

    monkeypatch.setattr(attach_mod, "pick_files", fake_pick)
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.click("#attach-btn")
        for _ in range(50):
            if app.attachments:
                break
            await pilot.pause()

        assert len(app.attachments) == 1
        assert app.attachments[0].kind == "image"
        bar = app.query_one("#attach-bar").render()
        assert "shot.png(图)" in bar.plain


async def test_tui_escape_clears_attachments(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(_png())
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.add_attachments([str(img)])
        assert app.attachments

        await pilot.press("escape")
        assert app.attachments == []


async def test_tui_add_attachments_dedupes_and_caps(tmp_path):
    paths = []
    for i in range(8):
        p = tmp_path / f"a{i}.png"
        p.write_bytes(_png())
        paths.append(str(p))
    app = _app(tmp_path)
    async with app.run_test():
        app.add_attachments(paths + paths)  # 重复两次
        assert len(app.attachments) == 6  # MAX_ATTACHMENTS


async def test_tui_submit_expands_attachments(tmp_path):
    from lcode.config import VisionConfig
    from lcode.tool.view_image import ViewImageTool

    img = tmp_path / "shot.png"
    img.write_bytes(_png())

    async def fake_asker(cfg, payloads, question, detail):
        return "第 12 行 TypeError"

    app = _app(tmp_path)
    app._tool_registry.register(
        ViewImageTool(VisionConfig(api_key="k", model="m"), asker=fake_asker)
    )
    async with app.run_test() as pilot:
        app.provider = FakeProvider("看完了")
        app.add_attachments([str(img)])
        await app.submit("这个报错是什么")

        for _ in range(100):
            if app.idle() and len(app.conv.messages()) >= 2:
                break
            await pilot.pause()

        first = app.conv.messages()[0]
        assert first.role == "user"
        assert "第 12 行 TypeError" in first.content
        assert "用户的问题：这个报错是什么" in first.content
        # 附件队列发送后清空，不会重复提交
        assert app.attachments == []


async def test_tui_submit_with_only_attachments(tmp_path):
    # 只有附件、没打字的输入也要能发出去
    from lcode.config import VisionConfig
    from lcode.tool.view_image import ViewImageTool

    img = tmp_path / "shot.png"
    img.write_bytes(_png())

    async def fake_asker(cfg, payloads, question, detail):
        return "图里有三行字"

    app = _app(tmp_path)
    app._tool_registry.register(
        ViewImageTool(VisionConfig(api_key="k", model="m"), asker=fake_asker)
    )
    async with app.run_test() as pilot:
        app.provider = FakeProvider("好的")
        app.add_attachments([str(img)])
        await app.submit("")
        for _ in range(100):
            if app.idle() and len(app.conv.messages()) >= 2:
                break
            await pilot.pause()
        assert "图里有三行字" in app.conv.messages()[0].content


async def test_tui_submit_fails_gracefully_without_vision(tmp_path):
    # 没配 vision：附件读不了，应该报错而不是把空内容发给模型
    img = tmp_path / "a.png"
    img.write_bytes(_png())
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.provider = FakeProvider("不该被调用")
        app.add_attachments([str(img)])
        await app.submit("看看这个")
        for _ in range(50):
            await pilot.pause()
            if not app.attachments:
                break
        assert app.conv.messages() == []  # 没有发起对话
        assert app.attachments == []


async def test_tui_slash_command_does_not_consume_attachments(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(_png())
    app = _app(tmp_path)
    async with app.run_test():
        captured = _capture(app)
        app.add_attachments([str(img)])
        await app.dispatch_slash("/status")
        # 斜杠命令不走模型，附件应保留待发送
        assert len(app.attachments) == 1
        assert any("Mode:" in msg for _, msg in captured)

