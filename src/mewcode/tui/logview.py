"""Selectable chat log.

Textual's drag-to-select machinery asks every widget for the plain text under
the selected cells. `RichLog` paints itself line by line and inherits the
default `Widget.get_selection`, which renders the whole widget as one Rich
renderable and therefore finds no text at all -- so the chat scrollback is
effectively unselectable.

`LogView` fixes this by slicing the strips RichLog already keeps, and it also
maps terminal *cell* offsets to characters (a CJK glyph is two cells wide),
which plain `Selection.extract` does not do.
"""

from __future__ import annotations

from rich.cells import cell_len
from textual.selection import Selection
from textual.widgets import RichLog


def slice_cells(line: str, start: int, end: int | None) -> str:
    """Slice `line` by terminal cell offsets; `end=None` means end of line."""
    if start <= 0 and end is None:
        return line
    selected: list[str] = []
    cell = 0
    for char in line:
        width = cell_len(char)
        if cell >= start and (end is None or cell < end):
            selected.append(char)
        cell += width
        if end is not None and cell >= end:
            break
    return "".join(selected)


class LogView(RichLog):
    """RichLog that can be selected with the mouse and copied with Ctrl+C."""

    def plain_text(self) -> str:
        """All logged lines as plain text (no trailing padding)."""
        return "\n".join(strip.text.rstrip() for strip in self.lines)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        lines = [strip.text.rstrip() for strip in self.lines]
        if not lines:
            return None
        start, end = selection
        if start is None and end is None:
            return "\n".join(lines), "\n"
        first = 0 if start is None else max(0, start.y)
        last = len(lines) - 1 if end is None else min(end.y, len(lines) - 1)
        if first > last or first >= len(lines):
            return None
        selected: list[str] = []
        for y in range(first, last + 1):
            span = selection.get_span(y)
            if span is None:
                continue
            start_x, end_x = span
            selected.append(slice_cells(lines[y], start_x, None if end_x == -1 else end_x))
        return "\n".join(selected), "\n"


__all__ = ["LogView", "slice_cells"]
