from __future__ import annotations

from lcode.command import Kind, Registry, register_builtins
from lcode.command.builtin_local import handle_memory, handle_permission, handle_session
from lcode.command.builtin_prompt import REVIEW_DIRECTIVE
from lcode.command.ui import NopUI
from lcode.permission import Mode
from lcode.prompt import EXECUTE_DIRECTIVE

EXPECTED = {
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


class RecordingUI(NopUI):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def println(self, msg: str) -> None:
        self.calls.append(("println", msg))
        super().println(msg)

    def error(self, msg: str) -> None:
        self.calls.append(("error", msg))
        super().error(msg)

    def set_mode(self, m: Mode) -> None:
        self.calls.append(("set_mode", m))
        super().set_mode(m)

    def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        self.calls.append(("inject_and_send", display_label, preset_prompt))
        super().inject_and_send(display_label, preset_prompt)

    def force_compact(self) -> None:
        self.calls.append(("force_compact",))

    def open_resume_menu(self) -> None:
        self.calls.append(("open_resume_menu",))

    async def clear_and_new_session(self) -> None:
        self.calls.append(("clear_and_new_session",))

    def quit(self) -> None:
        self.calls.append(("quit",))


def _registry() -> Registry:
    reg = Registry()
    register_builtins(reg)
    return reg


def test_register_builtins_all_registered():
    reg = _registry()
    visible = reg.visible()
    assert len(visible) == 18
    assert {c.name for c in visible} == EXPECTED
    assert [c.name for c in visible] == sorted(EXPECTED)
    assert all(c.name == c.name.lower() for c in visible)
    assert len({c.name for c in visible}) == 18


def test_register_builtins_no_collision():
    _registry()


def test_register_builtins_kinds():
    reg = _registry()
    kinds = {c.name: c.kind for c in reg.visible()}
    assert [n for n, k in kinds.items() if k is Kind.LOCAL] == [
        "audio",
        "copy",
        "help",
        "hooks",
        "image",
        "memory",
        "permission",
        "session",
        "status",
        "team",
    ]
    assert [n for n, k in kinds.items() if k is Kind.PROMPT] == ["do", "review"]
    assert [n for n, k in kinds.items() if k is Kind.UI] == [
        "clear",
        "compact",
        "exit",
        "plan",
        "resume",
        "worktree",
    ]


async def test_register_builtins_handlers_run_on_nop_ui():
    ui = RecordingUI()
    for cmd in _registry().visible():
        await cmd.handler(ui)


async def test_help_lists_all_twelve():
    ui = RecordingUI()
    await _registry().lookup("help").handler(ui)
    text = ui.printed[-1]
    for name in EXPECTED:
        assert f"/{name}" in text


async def test_handle_status_prints_all_keys():
    ui = RecordingUI()
    ui._memory_files = ["project: MEMORY.md"]
    ui._session_id = "sid"
    await _registry().lookup("status").handler(ui)
    text = ui.printed[-1]
    for key in ("Mode:", "Tokens:", "Tools:", "Memories:", "Model:", "Directory:"):
        assert key in text
    assert text.index("Mode:") < text.index("Tokens:") < text.index("Tools:")
    assert text.index("Memories:") < text.index("Model:") < text.index("Directory:")


async def test_handle_compact_blocks_when_busy():
    ui = RecordingUI()
    ui._idle = False
    await _registry().lookup("compact").handler(ui)
    assert ("error", "请等待当前任务完成") in ui.calls
    assert ("force_compact",) not in ui.calls


async def test_handle_compact_runs_when_idle():
    ui = RecordingUI()
    await _registry().lookup("compact").handler(ui)
    assert ("force_compact",) in ui.calls


async def test_handle_do_sets_mode_and_injects():
    ui = RecordingUI()
    ui.set_mode(Mode.PLAN)
    await _registry().lookup("do").handler(ui)
    assert ("set_mode", Mode.DEFAULT) in ui.calls
    assert ui.injected == [("/do", EXECUTE_DIRECTIVE)]


async def test_handle_review_injects_review_text():
    ui = RecordingUI()
    await _registry().lookup("review").handler(ui)
    assert ui.injected == [("/review", REVIEW_DIRECTIVE)]
    assert "审查" in ui.injected[0][1]


async def test_memory_permission_session_outputs():
    ui = RecordingUI()
    ui._memory_files = ["project: MEMORY.md", "user: prefs.md"]
    await handle_memory(ui)
    assert "project: MEMORY.md" in ui.printed[-1]
    assert "user: prefs.md" in ui.printed[-1]

    ui.set_mode(Mode.PLAN)
    await handle_permission(ui)
    assert ui.printed[-1] == "plan"

    ui._session_id = "20260101-000000-abcd"
    ui._session_path = "/tmp/session.jsonl"
    await handle_session(ui)
    assert ui.printed[-1] == "Session: 20260101-000000-abcd\nPath: /tmp/session.jsonl"


async def test_handle_hooks_lists_summaries():
    from lcode.command.ui import HookSummary

    ui = RecordingUI()
    ui._hook_summaries = [
        HookSummary(
            name="guard",
            event="PreToolUse",
            action_type="shell",
            only_once=True,
            source="/p/hooks.yaml",
        ),
        HookSummary(
            name="telemetry",
            event="PostToolUse",
            action_type="http",
            async_mode=True,
            source="/p/hooks.yaml",
        ),
    ]
    ui._hook_sources = ["/p/hooks.yaml"]
    await _registry().lookup("hooks").handler(ui)
    text = ui.printed[-1]
    assert "PreToolUse:" in text
    assert "- guard [once] (shell)" in text
    assert "- telemetry [async] (http)" in text
    assert "Loaded from:" in text
    assert "  - /p/hooks.yaml" in text


async def test_handle_hooks_empty():
    ui = RecordingUI()
    await _registry().lookup("hooks").handler(ui)
    assert ui.printed[-1] == "当前没有已加载的 Hook"


async def test_handle_memory_empty():
    ui = RecordingUI()
    await handle_memory(ui)
    assert ui.printed[-1] == "无已加载的记忆文件"
