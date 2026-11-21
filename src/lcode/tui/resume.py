"""Session resume picker and restore flow (F17-F24)."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import OptionList, Static

from lcode.compact import open_session_context
from lcode.compact.const import (
    AUTO_SAFETY_MARGIN,
    ESTIMATE_CHARS_PER_TOKEN,
    SUMMARY_RESERVE,
)
from lcode.compact.token import message_chars
from lcode.conversation import Conversation
from lcode.hook import Event as HookEvent
from lcode.llm import Message
from lcode.session import (
    SessionInfo,
    Writer,
    last_entry_ts,
    list_sessions,
    load_session,
    relative_time,
    size_label,
)
from lcode.tui.stream import ChatInput

RESUME_GAP_SECONDS = 6 * 3600


@dataclass
class SessionItem:
    info: SessionInfo

    @property
    def display_text(self) -> str:
        model = self.info.model or "unknown"
        return (
            f"{self.info.title} · {relative_time(self.info.modified_at)} · "
            f"{model} · {size_label(self.info.size)}"
        )


# ── entry / exit ─────────────────────────────────────────────────────────


def begin_resume(app) -> None:
    from lcode.tui.app import SessionState

    if app.state is not SessionState.IDLE:
        app.notify_system("请等待当前任务完成")
        return

    try:
        infos = list_sessions(app.sessions_dir)
    except OSError:
        infos = []
    if not infos:
        app.notify_system("（没有可恢复的会话）")
        return

    app.resume_items = [SessionItem(info) for info in infos]
    app.resume_query = ""
    app.state = SessionState.RESUMING
    app._show_selecting()
    app.query_one("#resume-query", Static).display = True
    render_resume_list(app)
    app.query_one("#select", OptionList).focus()


def cancel_resume(app) -> None:
    from lcode.tui.app import SessionState

    app.state = SessionState.IDLE
    app.resume_query = ""
    app.resume_visible = []
    try:
        app.query_one("#resume-query", Static).display = False
    except Exception:  # noqa: BLE001
        pass
    app._show_conversation()
    app.query_one("#input", ChatInput).focus()


# ── list rendering / filtering ───────────────────────────────────────────


def render_resume_list(app) -> None:
    option_list = app.query_one("#select", OptionList)
    option_list.clear_options()

    query = app.resume_query.strip().lower()
    visible = [item for item in app.resume_items if not query or query in item.display_text.lower()]
    app.resume_visible = visible
    for item in visible:
        option_list.add_option(item.display_text)
    if visible:
        option_list.highlighted = 0

    _update_hint(app)


def handle_resume_key(app, event) -> None:
    key = event.key
    if key == "escape":
        cancel_resume(app)
        event.stop()
        return
    if key in ("backspace", "ctrl+h"):
        app.resume_query = app.resume_query[:-1]
        render_resume_list(app)
        event.stop()
        return
    if key == "space":
        app.resume_query += " "
        render_resume_list(app)
        event.stop()
        return
    if len(key) == 1 and key.isprintable():
        app.resume_query += key
        render_resume_list(app)
        event.stop()


def _update_hint(app) -> None:
    query = app.resume_query
    hint = f"恢复会话：{query or '（输入文字可过滤）'}    Enter 选择 · Esc 取消"
    try:
        app.query_one("#resume-query", Static).update(Text(hint, style="dim"))
    except Exception:  # noqa: BLE001
        pass


# ── selection / restore ──────────────────────────────────────────────────


def select_session(app, index: int) -> None:
    if not 0 <= index < len(app.resume_visible):
        return
    item = app.resume_visible[index]
    app._resume_task = asyncio.create_task(do_resume_session(app, item))


async def do_resume_session(app, item: SessionItem) -> None:
    try:
        msgs = load_session(item.info.dir)
    except Exception as exc:  # noqa: BLE001
        _fail(app, f"恢复失败: {exc}")
        return

    msgs = await _maybe_compact(app, msgs)

    last_ts = last_entry_ts(item.info.dir)
    if last_ts is not None:
        gap = time.time() - last_ts
        if gap > RESUME_GAP_SECONDS:
            msgs = list(msgs) + [Message(role="user", content=_gap_notice(gap))]

    try:
        ctx = open_session_context(app.workspace, item.info.id)
        new_writer = Writer.open_existing(item.info.dir)
    except OSError as exc:
        _fail(app, f"恢复失败: {exc}")
        return

    new_writer.model = app.provider.model if app.provider is not None else ""
    new_conv = Conversation.from_messages(
        msgs,
        on_append=new_writer.on_append,
        on_replace=new_writer.on_replace,
    )

    previous_id = app.session_id()
    await app._dispatch_hook_event(HookEvent.SESSION_END, reason="resume")

    old_writer = app.writer
    app.conv = new_conv
    app.writer = new_writer
    app.ses_ctx = ctx
    app.runtime.session = ctx
    # 新会话开始：only_once 重新生效。
    if app.hook_engine is not None:
        app.hook_engine.reset_for_new_session()
    if old_writer is not None and old_writer is not new_writer:
        try:
            old_writer.close()
        except OSError:
            pass

    # F32：恢复聊天会话时同步 Worktree 会话的 active cwd。
    manager = getattr(app, "worktree_mgr", None)
    if manager is not None:
        active = manager.current_session()
        app.active_cwd = active.worktree_path if active is not None else ""

    cancel_resume(app)
    app._redraw_conversation()
    await app._dispatch_hook_event(
        HookEvent.SESSION_RESUME,
        previous_session_id=previous_id,
    )
    app.notify_system(f"已恢复会话 {item.info.id}，共 {len(msgs)} 条消息")


def _fail(app, message: str) -> None:
    cancel_resume(app)
    app.notify_system(message)


# ── helpers ──────────────────────────────────────────────────────────────


async def _maybe_compact(app, msgs: list[Message]) -> list[Message]:
    # 加载后估算 token，超过 context_window - summary_reserve - safety_margin 时压缩。
    if app.context_window <= 0:
        return msgs
    threshold = app.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
    if threshold <= 0:
        return msgs
    estimated = math.ceil(message_chars(msgs) / ESTIMATE_CHARS_PER_TOKEN)
    if estimated <= threshold:
        return msgs

    if app.compact_fn is None:
        app.notify_system("（上下文较大：自动压缩尚未启用，已按原样恢复）")
        return msgs
    await app._dispatch_hook_event(HookEvent.PRE_COMPACT, reason="resume", message_count=len(msgs))
    try:
        compacted = await app.compact_fn(msgs)
    except Exception:  # noqa: BLE001 - a failed compaction must not block resume
        app.notify_system("（恢复时压缩失败，已按原样恢复）")
        return msgs
    if compacted is None:
        return msgs
    await app._dispatch_hook_event(
        HookEvent.POST_COMPACT, reason="resume", message_count=len(compacted)
    )
    return compacted


def _gap_notice(seconds: float) -> str:
    return (
        f"[系统提示] 本会话已暂停 {_humanize(seconds)}。"
        "部分上下文可能已过时，如需最新信息请重新读取相关文件。"
    )


def _humanize(seconds: float) -> str:
    if seconds >= 86400:
        return f"{int(seconds // 86400)} 天"
    if seconds >= 3600:
        return f"{int(seconds // 3600)} 小时"
    return f"{int(seconds // 60)} 分钟"


__all__ = [
    "SessionItem",
    "begin_resume",
    "cancel_resume",
    "do_resume_session",
    "handle_resume_key",
    "render_resume_list",
    "select_session",
]
