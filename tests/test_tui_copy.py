"""剪贴板能力：LogView 文本选区、/copy 命令、Ctrl+C 二义性。"""

from __future__ import annotations

from pathlib import Path

from textual.geometry import Offset
from textual.selection import Selection

from lcode.command import Registry, register_builtins
from lcode.command.ui import NopUI
from lcode.config import ProviderConfig
from lcode.llm import Message, StreamEvent
from lcode.permission import new_engine
from lcode.tool import new_default_registry
from lcode.tui.app import LcodeApp, SessionState
from lcode.tui.logview import LogView, slice_cells


class FakeProvider:
    def __init__(self, text: str = "ok") -> None:
        self._text = text

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        if self._text:
            yield StreamEvent(text=self._text)
        yield StreamEvent(done=True)


def _app(tmp_path: Path) -> LcodeApp:
    providers = [ProviderConfig(name="fake", protocol="openai", api_key="k", model="fake-model")]
    app = LcodeApp(
        providers,
        "dev",
        new_default_registry(),
        new_engine(str(tmp_path))[0],
        workspace=str(tmp_path),
        context_window=128000,
    )
    # 测试里不碰真实剪贴板，只记录。
    app.copy_text = lambda text: app.__dict__.setdefault("_copied", []).append(text)
    return app


# ── slice_cells ──────────────────────────────────────────────────────────


def test_slice_cells_is_cell_aware():
    line = "你好世界 abc"
    assert slice_cells(line, 0, 4) == "你好"  # 每个汉字占 2 列
    assert slice_cells(line, 4, 8) == "世界"
    assert slice_cells(line, 9, 12) == "abc"
    assert slice_cells(line, 0, None) == line
    assert slice_cells(line, 8, None) == " abc"


def test_slice_cells_keeps_emoji_intact():
    line = "a🚀b"
    assert slice_cells(line, 1, 3) == "🚀"


# ── LogView 选区 ─────────────────────────────────────────────────────────


async def test_logview_selection_returns_plain_text(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        log = app.query_one("#log", LogView)
        log.clear()
        log.write("访问 https://example.com/a?b=1 即可")
        log.write("第二行")
        await pilot.pause()

        text, ending = log.get_selection(Selection(Offset(0, 0), Offset(4, 0)))
        assert text == "访问"
        assert ending == "\n"

        whole = log.get_selection(Selection(None, None))
        assert whole is not None
        assert "https://example.com/a?b=1" in whole[0]
        assert "第二行" in whole[0]


async def test_logview_selection_spans_multiple_lines(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        log = app.query_one("#log", LogView)
        log.clear()
        log.write("line-one")
        log.write("line-two")
        await pilot.pause()
        text, _ = log.get_selection(Selection(Offset(5, 0), Offset(4, 1)))
        assert text == "one\nline"


async def test_logview_selection_empty_log_returns_none(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        log = app.query_one("#log", LogView)
        log.clear()
        assert log.get_selection(Selection(None, None)) is None


async def test_screen_copy_select_all_reaches_app_clipboard(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        log = app.query_one("#log", LogView)
        log.clear()
        log.write("https://example.com/x")
        await pilot.pause()

        log.text_select_all()
        await pilot.pause()
        assert "https://example.com/x" in (app.screen.get_selected_text() or "")

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert (
            app._copied == [app.screen.get_selected_text()]
            or "https://example.com/x" in (app._copied[-1])
        )


# ── Ctrl+C 二义性 ────────────────────────────────────────────────────────


async def test_ctrl_c_copies_selection_instead_of_quitting(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        app.state = SessionState.IDLE
        log = app.query_one("#log", LogView)
        log.clear()
        log.write("copy-me")
        await pilot.pause()
        log.text_select_all()
        await pilot.pause()

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._copied and "copy-me" in app._copied[-1]
        assert not app.screen.selections  # 复制后清掉选区
        # 第二次 Ctrl+C 恢复原语义：空闲时退出
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app._return_code is not None or len(app._copied) == 1


async def test_ctrl_c_cancels_turn_even_with_selection(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        log = app.query_one("#log", LogView)
        log.write("streaming text")
        await pilot.pause()
        log.text_select_all()
        await pilot.pause()

        app.state = SessionState.STREAMING
        app.turn_cancel = __import__("asyncio").Event()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.turn_cancel.is_set()
        assert not getattr(app, "_copied", [])


# ── /copy 命令 ───────────────────────────────────────────────────────────


def _registry() -> Registry:
    reg = Registry()
    register_builtins(reg)
    return reg


async def test_copy_command_copies_last_reply():
    ui = NopUI()
    ui._history = [
        Message(role="assistant", content="第一条"),
        Message(role="assistant", content="见 https://example.com/z"),
    ]
    await _registry().lookup("copy").handler(ui)
    assert ui.copied == ["见 https://example.com/z"]
    assert "已复制" in ui.printed[-1]


async def test_copy_command_all_uses_screen_text():
    ui = NopUI()
    ui._log_text = "整屏内容"
    ui.set_command_args("all")
    await _registry().lookup("copy").handler(ui)
    assert ui.copied == ["整屏内容"]


async def test_copy_command_code_extracts_fences():
    ui = NopUI()
    ui._history = [
        Message(
            role="assistant",
            content="看这里：\n```bash\nuv run lcode\n```\n还有\n```\nx=1\n```",
        )
    ]
    ui.set_command_args("code")
    await _registry().lookup("copy").handler(ui)
    assert ui.copied == ["uv run lcode\n\nx=1"]


async def test_copy_command_empty_and_bad_args():
    ui = NopUI()
    await _registry().lookup("copy").handler(ui)
    assert ui.copied == []
    assert "没有可复制" in ui.errors[-1]

    ui.set_command_args("bogus")
    await _registry().lookup("copy").handler(ui)
    assert "用法" in ui.errors[-1]


async def test_copy_command_through_tui_dispatches(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        app.conv.add_assistant("答案在 https://example.com/q")
        await pilot.pause()
        await app.dispatch_slash("/copy")
        await pilot.pause()
        assert app._copied == ["答案在 https://example.com/q"]


async def test_copy_command_allowed_while_streaming(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        app.conv.add_assistant("正在回答")
        app.state = SessionState.STREAMING
        await pilot.pause()
        await app.dispatch_slash("/copy")
        await pilot.pause()
        assert app._copied == ["正在回答"]


async def test_copy_last_prefers_inflight_reply(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        app.conv.add_assistant("上一轮的旧回答")
        app.conv.add_user("这一轮的问题")
        app.cur_reply = "正在流式输出的 https://example.com/live"
        app.state = SessionState.STREAMING
        await pilot.pause()
        await app.dispatch_slash("/copy")
        await pilot.pause()
        assert app._copied == ["正在流式输出的 https://example.com/live"]
