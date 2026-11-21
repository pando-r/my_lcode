"""Input widget and streaming helpers for the TUI."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class ChatInput(TextArea):
    # Multi-line input where Enter submits and Alt+Enter inserts a newline.

    class Submitted(Message):
        # Posted when the user presses Enter without the Alt modifier.

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        # 补全菜单激活时优先消费键位，避免 TextArea 先移动光标/插入字符。
        handler = getattr(self.app, "handle_completion_key", None)
        if handler is not None and handler(event):
            event.prevent_default()
            event.stop()
            return
        if event.key == "enter" or event.key == "alt+enter":
            is_alt_enter = event.key == "alt+enter" or bool(getattr(event, "alt", False))
            if is_alt_enter:
                self.insert("\n")
            else:
                self.post_message(self.Submitted(self.text))
            event.prevent_default()
            return
        await super()._on_key(event)
