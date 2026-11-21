"""Rendering helpers for the TUI."""

from __future__ import annotations

from rich.markdown import Markdown
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from lcode.permission import Mode

UI_SUMMARY_LINES = 8


def user_block(text: str) -> Text:
    # Render a completed user message (no You/Lcode label).
    return Text("● " + text, style="bold")


def render_markdown(reply: str) -> Markdown:
    # Render a completed assistant reply as Markdown.
    return Markdown(reply)


def error_block(err: Exception | str) -> Text:
    # Render an error message with a distinguishable style.
    return Text("● " + str(err), style="bold red")


def tool_line(name: str, args: str) -> Text:
    # Render a tool-call line in Claude Code style: ● name(args).
    text = Text("● ", style="bold cyan")
    text.append(f"{name}({args})", style="bold")
    return text


def tool_result_summary(result: str, is_error: bool = False) -> Padding:
    # Render a (UI-truncated) tool result summary, indented under the tool line.
    lines = result.splitlines()
    if len(lines) > UI_SUMMARY_LINES:
        result = "\n".join(lines[:UI_SUMMARY_LINES]) + "\n…"
    style = "red" if is_error else "dim"
    return Padding(Text("⎿ " + result, style=style), (0, 0, 0, 2))


def approval_block(req, cursor: int) -> Text:
    # Render the human-in-the-loop approval menu.
    text = Text()
    text.append(f"● {req.name}({req.args})\n", style="bold cyan")
    text.append(f"  {req.reason}\n", style="dim")
    text.append("  是否继续?\n", style="bold")
    labels = [
        "1. 允许本次",
        "2. 永久允许（写入本地配置）",
        "3. 拒绝本次",
    ]
    for i, label in enumerate(labels):
        if i == cursor:
            text.append(f"> {label}\n", style="bold green")
        else:
            text.append(f"  {label}\n", style="")
    text.append("  ↑↓ 选择 · 回车确认 · Esc 取消", style="dim")
    return text


def render_statusbar(
    mode: Mode,
    model: str,
    usage_in: int = 0,
    usage_out: int = 0,
    coordinator: bool = False,
) -> Table:
    # Render the status bar: permission mode on the left, model + usage on the right.
    mode_styles = {
        Mode.DEFAULT: "bold green",
        Mode.ACCEPT_EDITS: "bold cyan",
        Mode.PLAN: "bold yellow",
        Mode.BYPASS: "bold red",
    }
    left = Text(str(mode).upper(), style=mode_styles.get(mode, "bold"))
    if coordinator:
        left.append(" [COORDINATOR]", style="bold magenta")

    right = Text()
    right.append(model, style="bold magenta")
    if usage_in or usage_out:
        right.append(f"  ↑{_compact(usage_in)} ↓{_compact(usage_out)} tok", style="dim")

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1, justify="left")
    grid.add_column(ratio=1, justify="right")
    grid.add_row(left, right)
    return grid


def _compact(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"
