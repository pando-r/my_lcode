"""Inline slash-command completion menu."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from rich.text import Text
from textual.widgets import Static

from lcode.command import Command, Registry

MAX_ROWS = 8
NO_MATCH_HINT = "无匹配（Esc 关闭）"


@dataclass
class CompletionMenu:
    items: list[Command] = field(default_factory=list)
    cursor: int = 0
    offset: int = 0
    active: bool = False

    def update(self, input_text: str, reg: Registry) -> None:
        # 仅首字符为 "/" 且单行时激活；前缀匹配只看命令名。
        if "\n" in input_text or not input_text.startswith("/"):
            self.hide()
            return
        self.items = reg.prefix_match(input_text)
        self.active = True
        if self.cursor >= len(self.items):
            self.cursor = max(0, len(self.items) - 1)
        self._clamp_offset()

    def move_up(self) -> None:
        if not self.items:
            return
        self.cursor = max(0, self.cursor - 1)
        self._clamp_offset()

    def move_down(self) -> None:
        if not self.items:
            return
        self.cursor = min(len(self.items) - 1, self.cursor + 1)
        self._clamp_offset()

    def selected(self) -> Command | None:
        if not self.items or not 0 <= self.cursor < len(self.items):
            return None
        return self.items[self.cursor]

    def hide(self) -> None:
        self.active = False
        self.items = []
        self.cursor = 0
        self.offset = 0

    def render(self, width: int) -> Text:
        if not self.active:
            return Text()
        if not self.items:
            return Text(NO_MATCH_HINT, style="dim")

        name_width = max(len(cmd.name) for cmd in self.items)
        visible = self.items[self.offset : self.offset + MAX_ROWS]
        block = Text()

        if self.offset > 0:
            block.append(f"  ↑ {self.offset} more\n", style="dim")
        for i, cmd in enumerate(visible):
            index = self.offset + i
            line = Text()
            line.append("▶ " if index == self.cursor else "  ")
            line.append(cmd.name.ljust(name_width + 1), style="bold")
            line.append(cmd.description)
            if index == self.cursor:
                line.stylize("reverse")
            line.truncate(width)
            block.append_text(line)
            block.append("\n")
        remaining = len(self.items) - (self.offset + len(visible))
        if remaining > 0:
            block.append(f"  ↓ {remaining} more\n", style="dim")
        return block

    def _clamp_offset(self) -> None:
        if not self.items:
            self.offset = 0
            return
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + MAX_ROWS:
            self.offset = self.cursor - MAX_ROWS + 1
        max_offset = max(0, len(self.items) - MAX_ROWS)
        self.offset = max(0, min(self.offset, max_offset))


class CompletionMixin:
    # LcodeApp 通过本 mixin 接入补全菜单的键位、输入同步与渲染。

    def handle_completion_key(self, event) -> bool:
        # 返回 True 表示该键已被菜单消费，ChatInput 不再交给 TextArea。
        if not self.completion.active:
            return False

        if event.key == "up":
            self.completion.move_up()
            self._render_completion()
            return True
        if event.key == "down":
            self.completion.move_down()
            self._render_completion()
            return True
        if event.key == "escape":
            self.completion.hide()
            self._render_completion()
            return True
        if event.key in ("enter", "tab"):
            selected = self.completion.selected()
            if selected is None:
                self.completion.hide()
                self._render_completion()
                # 零匹配：Tab 只关菜单；Enter 透传给正常提交，走未命中提示。
                return event.key == "tab"
            self._execute_selected(selected)
            return True
        return False

    def on_text_area_changed(self, event) -> None:
        text_area = getattr(event, "text_area", None)
        if text_area is None or getattr(text_area, "id", None) != "input":
            return
        self._sync_completion_from_input()

    def _sync_completion_from_input(self) -> None:
        try:
            area = self.query_one("#input")
            text = area.text
        except Exception:  # noqa: BLE001 - not mounted yet
            return
        self.completion.update(text, self.cmd_registry)
        self._render_completion()

    def _execute_selected(self, cmd: Command) -> None:
        self.completion.hide()
        self._render_completion()
        try:
            self.query_one("#input").load_text("")
        except Exception:  # noqa: BLE001
            pass
        self._selected_task = asyncio.create_task(self._run_selected(cmd))

    async def _run_selected(self, cmd: Command) -> None:
        try:
            await self.submit("/" + cmd.name)
        finally:
            self.completion.hide()
            self._render_completion()

    def _render_completion(self) -> None:
        try:
            widget = self.query_one("#completion", Static)
        except Exception:  # noqa: BLE001 - not mounted yet
            return
        if not self.completion.active:
            widget.update("")
            return
        width = self.size.width if self.size.width > 0 else 80
        widget.update(self.completion.render(width))
