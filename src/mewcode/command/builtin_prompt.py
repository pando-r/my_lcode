"""Prompt commands: /do /review (inject a user message, then start a turn)."""

from __future__ import annotations

from mewcode.command.ui import UI
from mewcode.permission import Mode
from mewcode.prompt import EXECUTE_DIRECTIVE

REVIEW_DIRECTIVE = (
    "请审查当前上下文中的代码改动或已读取的文件，指出潜在 bug、可读性问题与可简化之处。"
)


async def handle_do(ui: UI) -> None:
    ui.set_mode(Mode.DEFAULT)
    ui.inject_and_send("/do", EXECUTE_DIRECTIVE)


async def handle_review(ui: UI) -> None:
    ui.inject_and_send("/review", REVIEW_DIRECTIVE)
